"""Export vers le jeu PUBLIC : seuls les participants avec consent_publication == True sortent.

Lancer : .venv/bin/python -B -m ubri_nrd.exporter <jeu source> <dossier de sortie neuf>
Codes : 0 export fait (et refus éventuels nommés) ; 3 RIEN à publier (refus total, jamais un succès muet, KE#105) ;
        1 erreur (source invalide, sortie existante, contrôle de sortie en échec).

Consentements exigés : consent_publication, ET consent_usage_commercial si la licence du jeu autorise l'usage
commercial (toute licence sans « -NC »). Deux jambes indépendantes :
  1. filtre d'entrée sur participants.parquet (booléen True exact, jamais « vrai-ish ») ;
  2. contrôle de SORTIE qui relit le jeu écrit sur disque et refuse s'il contient un seul participant sans
     consentement de publication, ou sans consentement d'usage commercial quand la licence n'est pas NC, un épisode
     de participant absent, ou un chemin EEG non dérivé de son épisode.
"""
import shutil
import sys
from pathlib import Path

import numpy as np

from .io import ecrire_json, ecrire_parquet, eeg_relpath, lire_info, lire_montage, lire_table
from .valider import valider


class ExportRefuse(Exception):
    pass


def _consenti(v) -> bool:
    return isinstance(v, (bool, np.bool_)) and bool(v) is True


def _licence_commerciale(licence) -> bool:
    return "-NC" not in str(licence).upper()


def controle_sortie(dest: Path) -> None:
    """Jambe 2 : relit le jeu exporté. Indépendante du filtre d'entrée (ne lit QUE la sortie, et n'appelle ni
    _consenti ni _licence_commerciale)."""
    part = lire_table(dest, "meta/participants.parquet")
    ep = lire_table(dest, "neural/episodes.parquet")
    # Vérification écrite à part, sans _consenti : si la jambe 1 se trompe, celle-ci ne se trompe pas avec elle.
    interdits = sorted(pid for pid, v in zip(part["participant_id"], part["consent_publication"])
                       if not (type(v) in (bool, np.bool_) and v == True))  # noqa: E712
    if interdits:
        raise ExportRefuse(f"sortie : participants sans consentement de publication : {interdits}")
    if "-NC" not in str(lire_info(dest).get("licence")).upper():
        sans_com = sorted(pid for pid, v in zip(part["participant_id"], part["consent_usage_commercial"])
                          if not (type(v) in (bool, np.bool_) and v == True))  # noqa: E712
        if sans_com:
            raise ExportRefuse(f"sortie : licence commerciale, participants sans consentement d'usage commercial : "
                               f"{sans_com}")
    orphelins = sorted(set(ep["participant_id"]) - set(part["participant_id"]))
    if orphelins:
        raise ExportRefuse(f"sortie : épisodes de participants absents (consentement inconnu) : {orphelins}")
    derives = [eeg_relpath(int(i)) for i in ep["episode_index"]]
    if list(ep["eeg_file"]) != derives or len(set(derives)) != len(derives):
        raise ExportRefuse("sortie : eeg_file différent du chemin dérivé de l'épisode, ou partagé")
    fichiers = {str(p.relative_to(dest)) for p in (dest / "neural/eeg").rglob("*.parquet")}
    if fichiers != set(derives):
        raise ExportRefuse("sortie : fichiers EEG différents des épisodes déclarés")


def exporter_public(src: Path, dest: Path) -> dict:
    src, dest = Path(src), Path(dest)
    err, _ = valider(src, verifier_eeg=False)
    if err:
        raise ExportRefuse(f"source invalide ({len(err)} erreur(s)), première : {err[0]}")
    if dest.exists():
        raise ExportRefuse(f"{dest} existe déjà : l'export écrit toujours dans un dossier neuf")
    part = lire_table(src, "meta/participants.parquet")
    ep = lire_table(src, "neural/episodes.parquet")
    evt = lire_table(src, "neural/events.parquet")

    commercial = _licence_commerciale(lire_info(src).get("licence"))
    ok = part["consent_publication"].map(_consenti)  # jambe 1
    if commercial:
        ok &= part["consent_usage_commercial"].map(_consenti)
    garder = set(part.loc[ok, "participant_id"])
    refuses = sorted(set(part["participant_id"]) - garder)
    rapport = {"licence commerciale": commercial, "publiés": sorted(garder),
               "refusés (consentement manquant)": refuses}
    if not garder:
        rapport["statut"] = "rien_a_publier"
        return rapport

    ep_out = ep[ep["participant_id"].isin(garder)]
    evt_out = evt[evt["episode_index"].isin(set(ep_out["episode_index"]))]
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():  # jamais supprimer un dossier que cette exécution n'a pas créé
        raise ExportRefuse(f"{tmp} existe (export précédent interrompu ?) : à examiner puis retirer à la main")
    tmp.mkdir(parents=True)
    try:
        for i in ep_out["episode_index"]:
            f = eeg_relpath(int(i))  # chemin DÉRIVÉ (KE#283), jamais la valeur déclarée
            (tmp / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / f, tmp / f)
        ecrire_parquet(tmp / "meta/participants.parquet", part[part["participant_id"].isin(garder)])
        ecrire_parquet(tmp / "neural/episodes.parquet", ep_out)
        ecrire_parquet(tmp / "neural/events.parquet", evt_out)
        ecrire_json(tmp / "meta/montage.json", lire_montage(src))
        ecrire_json(tmp / "meta/info.json", {"ubri_nrd": lire_info(src)})
        controle_sortie(tmp)  # jambe 2, AVANT la bascule
        err, _ = valider(tmp)
        if err:
            raise ExportRefuse(f"sortie invalide ({len(err)} erreur(s)), première : {err[0]}")
    except Exception:
        shutil.rmtree(tmp)
        raise
    tmp.rename(dest)  # bascule atomique (même dossier parent)
    rapport["statut"] = "exporte"
    return rapport


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        r = exporter_public(Path(argv[1]), Path(argv[2]))
    except ExportRefuse as e:
        print(f"EXPORT REFUSÉ : {e}")
        return 1
    for k, v in r.items():
        print(f"{k} : {v}")
    return 3 if r["statut"] == "rien_a_publier" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
