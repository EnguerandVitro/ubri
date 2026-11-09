"""Validateur UBRI-NRD v0.2 (trous dans l'EEG : colonne idx, étendue et pertes annoncées par épisode). Lancer : .venv/bin/python -B -m ubri_nrd.valider <dossier du jeu>

Chaque contrôle annonce combien d'éléments il a vérifiés (KE#111 : un contrôle qui passe à vide doit se voir).
Sortie : code 0 si aucune erreur, 1 sinon. Une erreur est nommée (fichier, épisode, champ), jamais résumée.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .io import eeg_relpath, lire_info, lire_montage, lire_table
from .schema import (AGENTS, CONSENT_FIELDS, EPISODE_COLUMNS, ERROR_KINDS, EVENT_COLUMNS, EVENT_OPTIONAL, EVENT_TYPES,
                     INFO_KEYS,
                     PARTICIPANT_COLUMNS, VERSION)

TOL_DT = 1e-6  # écart toléré sur les bornes et horodatages (s)
TOL_PPM = 1000.0  # écart toléré entre le pas réel et 1/fs nominal : dérive d'un quartz (±100 ppm typique)
BOOL_FIELDS = {"planned", "target", "saw_error"}


def valider(root: Path, verifier_eeg: bool = True) -> tuple[list[str], dict]:
    err: list[str] = []
    n: dict[str, int] = {}
    root = Path(root)
    if not (root / "meta/info.json").is_file():
        return [f"{root} : meta/info.json absent (jeu incomplet ou écriture interrompue)"], n

    info = lire_info(root)
    for k in INFO_KEYS:
        if k not in info:
            err.append(f"info.json : clé « {k} » absente")
    if info.get("version") != VERSION:
        err.append(f"info.json : version {info.get('version')!r}, attendu {VERSION!r}"
                   + (" (jeu v0.1 : à reconvertir, la v0.2 ajoute la colonne idx et idx_start/idx_end/pertes_annoncees)"
                      if info.get("version") == "0.1" else ""))
    if info.get("agent") not in AGENTS:
        err.append(f"info.json : agent {info.get('agent')!r} hors vocabulaire {sorted(AGENTS)}")
    fs = info.get("fs")
    if isinstance(fs, bool) or not isinstance(fs, (int, float)) or fs <= 0:
        err.append(f"info.json : fs {fs!r} invalide")
        fs = None

    montage = lire_montage(root)
    channels = montage.get("channels")
    if not channels or len(set(channels)) != len(channels):
        err.append("montage.json : liste « channels » vide ou avec doublons")
        channels = channels or []

    part = lire_table(root, "meta/participants.parquet")
    if tuple(part.columns) != PARTICIPANT_COLUMNS:
        err.append(f"participants.parquet : colonnes {list(part.columns)}, attendu {list(PARTICIPANT_COLUMNS)}")
    if len(part) == 0 or "participant_id" not in part:  # AVANT tout accès par colonne (table vide = KeyError)
        return err + ["participants.parquet : aucun participant (jeu vide)"], n
    for c in CONSENT_FIELDS:
        if c in part:
            bad = part[~part[c].map(lambda v: isinstance(v, (bool, np.bool_)))]
            for pid in bad["participant_id"]:
                err.append(f"participants.parquet : {pid} : {c} n'est pas un booléen (consentement indéterminé)")
    if "consent_collecte" in part:
        for pid, v in zip(part["participant_id"], part["consent_collecte"]):
            if not (type(v) in (bool, np.bool_) and v == True):  # noqa: E712
                err.append(f"participants.parquet : {pid} : consent_collecte n'est pas True (donnée non collectable)")
    if part["participant_id"].duplicated().any():
        err.append("participants.parquet : participant_id en double")
    n["participants"] = len(part)

    ep = lire_table(root, "neural/episodes.parquet")
    if tuple(ep.columns) != EPISODE_COLUMNS:
        err.append(f"episodes.parquet : colonnes {list(ep.columns)}, attendu {list(EPISODE_COLUMNS)}")
        return err, n
    if ep["episode_index"].duplicated().any():
        err.append("episodes.parquet : episode_index en double")
    inconnus = set(ep["participant_id"]) - set(part["participant_id"])
    for pid in sorted(inconnus):
        err.append(f"episodes.parquet : participant {pid} absent de participants.parquet")
    # Le chemin EEG est DÉRIVÉ de l'épisode, jamais lu tel que déclaré (KE#283) : unique, sans traversée, et un
    # épisode ne peut pas désigner le fichier d'un autre participant.
    for r in ep.itertuples():
        attendu = eeg_relpath(int(r.episode_index))
        if r.eeg_file != attendu:
            err.append(f"épisode {r.episode_index} : eeg_file {r.eeg_file!r}, attendu {attendu!r}")
    # Couverture : chaque épisode déclaré a son fichier EEG, et aucun fichier EEG n'est orphelin.
    declares = set(ep["eeg_file"])
    eeg_paths = list((root / "neural/eeg").rglob("*.parquet"))
    for f in eeg_paths:
        if f.is_symlink():
            err.append(f"EEG : {f.relative_to(root)} est un lien symbolique (refusé)")
    presents = {str(f.relative_to(root)) for f in eeg_paths if not f.is_symlink()}
    for f in sorted(declares - presents):
        err.append(f"EEG manquant : {f}")
    for f in sorted(presents - declares):
        err.append(f"EEG orphelin (aucun épisode) : {f}")
    n["épisodes"] = len(ep)
    if len(ep) == 0:
        err.append("episodes.parquet : aucun épisode (jeu vide)")

    # Étendue et pertes annoncées : contrôlées sur la table, AVANT les fichiers (un épisode sans fichier est déjà nommé).
    etendue_ok = set()
    for r in ep.itertuples():
        where = f"épisode {r.episode_index}"
        if not all(isinstance(v, (int, np.integer)) and not isinstance(v, bool) for v in (r.idx_start, r.idx_end)):
            err.append(f"{where} : idx_start / idx_end non entiers")
        elif r.idx_start > r.idx_end:
            err.append(f"{where} : idx_start {r.idx_start} > idx_end {r.idx_end}")
        else:
            etendue_ok.add(r.episode_index)
        pa_ = r.pertes_annoncees
        if not (pa_ is None or pa_ is pd.NA or (isinstance(pa_, float) and pa_ != pa_)) and (
                isinstance(pa_, bool) or not isinstance(pa_, (int, np.integer)) or pa_ < 0):
            err.append(f"{where} : pertes_annoncees {pa_!r} : ni vide ni entier positif")
    manquants_total, trous_total, hote_total, idx_par_ep = 0, 0, 0, {}
    if verifier_eeg:
        n["fichiers EEG vérifiés"] = 0
        for row in ep.itertuples():
            if row.eeg_file not in presents or row.episode_index not in etendue_ok:
                continue  # étendue invalide : déjà nommée plus haut, jamais int(pd.NA)
            df = lire_table(root, row.eeg_file)  # une seule lecture par fichier
            if list(df.columns) != ["t", "idx", *channels]:
                err.append(f"épisode {row.episode_index} ({row.eeg_file}) : colonnes {list(df.columns)}, "
                           f"attendu t + idx + montage")
                continue
            t, idx, x = df["t"].to_numpy(), df["idx"].to_numpy(), df[channels].to_numpy()
            n["fichiers EEG vérifiés"] += 1
            where = f"épisode {row.episode_index} ({row.eeg_file})"
            if len(t) != row.n_samples:
                err.append(f"{where} : {len(t)} échantillons, déclaré {row.n_samples}")
            if idx.dtype != np.int64:  # uint64 : np.diff boucle et « croissant » passerait sur une suite décroissante
                err.append(f"{where} : idx de type {idx.dtype}, attendu int64")
                continue
            if len(idx) and not np.all(np.diff(idx) > 0):
                err.append(f"{where} : idx non strictement croissant (doublon ou désordre)")
                continue
            i0, i1 = int(row.idx_start), int(row.idx_end)
            if len(idx) and (idx[0] < i0 or idx[-1] > i1):
                err.append(f"{where} : idx {idx[0]}..{idx[-1]} hors de [idx_start, idx_end] = [{i0}, {i1}]")
                continue
            if not np.all(np.isfinite(t)) or not np.all(np.diff(t) > 0):
                err.append(f"{where} : horodatage non fini ou non strictement croissant")
            elif fs and len(t) > 1:
                # pas mesuré PAR ÉCHANTILLON D'APPAREIL : vaut aussi à travers un trou (didx > 1)
                ecart = np.abs(np.diff(t) / np.diff(idx) * fs - 1) * 1e6
                if ecart.max() > TOL_PPM:
                    err.append(f"{where} : pas d'échantillonnage différent de 1/fs (écart max {ecart.max():.0f} ppm > "
                               f"{TOL_PPM:.0f}), trous compris")
            if not np.all(np.isfinite(x)):
                err.append(f"{where} : valeurs EEG non finies")
            if len(t) and fs:
                # t_start / t_end = heure de idx_start / idx_end, extrapolée si ces échantillons manquent
                for nom, borne, k, tk in (("t_start", row.t_start, i0, t[0] - (idx[0] - i0) / fs),
                                          ("t_end", row.t_end, i1, t[-1] + (i1 - idx[-1]) / fs)):
                    tol = TOL_DT + TOL_PPM * 1e-6 * abs(k - (idx[0] if nom == "t_start" else idx[-1])) / fs
                    if abs(tk - borne) > tol:
                        err.append(f"{where} : {nom} {borne:.6f} incohérent avec idx (attendu {tk:.6f} ± {tol:.6f})")
            manquants = (i1 - i0 + 1) - len(idx)
            trous = (int(np.sum(np.diff(idx) > 1)) + int(idx[0] > i0) + int(idx[-1] < i1)) if len(idx) else 1
            manquants_total += manquants
            trous_total += trous
            idx_par_ep[row.episode_index] = (t, idx, i0, i1)
            pa_ = row.pertes_annoncees
            if not (pa_ is None or pa_ is pd.NA or (isinstance(pa_, float) and pa_ != pa_)):
                # Le format enregistre la VÉRITÉ : la source ne voit pas les pertes de l'hôte (pile BLE, tampon LSL,
                # décodeur). manquants > annoncées = pertes côté hôte, COMPTÉES et affichées (jamais un refus qui
                # pousserait à vider l'annonce, KE#105) ; manquants < annoncées = annonce ou étendue fausse : erreur.
                if manquants < pa_:
                    err.append(f"{where} : {manquants} manquants < {pa_} pertes annoncées (annonce ou étendue fausse)")
                else:
                    hote_total += manquants - int(pa_)
        n["échantillons manquants"] = manquants_total
        n["trous (début, milieu, fin)"] = trous_total
        n["pertes non annoncées par la source (côté hôte)"] = hote_total

    if verifier_eeg and n["fichiers EEG vérifiés"] != len(ep):
        err.append(f"EEG : {n['fichiers EEG vérifiés']} fichiers vérifiés pour {len(ep)} épisodes")

    evt = lire_table(root, "neural/events.parquet")
    if tuple(evt.columns) != EVENT_COLUMNS:
        err.append(f"events.parquet : colonnes {list(evt.columns)}, attendu {list(EVENT_COLUMNS)}")
        return err, n
    bornes = {r.episode_index: (r.t_start, r.t_end) for r in ep.itertuples()}
    par_type: dict[str, int] = {}
    for i, e in enumerate(evt.itertuples()):
        where = f"événement {i} (épisode {e.episode_index}, t={e.t:.6f})"
        if e.type not in EVENT_TYPES:
            err.append(f"{where} : type {e.type!r} hors vocabulaire")
            continue
        par_type[e.type] = par_type.get(e.type, 0) + 1
        try:
            d = json.loads(e.details)
        except (TypeError, json.JSONDecodeError):
            err.append(f"{where} : details n'est pas un JSON valide")
            continue
        if not isinstance(d, dict):
            err.append(f"{where} : details n'est pas un objet JSON")
            continue
        manque = EVENT_TYPES[e.type] - set(d)
        if manque:
            err.append(f"{where} : {e.type} sans {sorted(manque)}")
        extra = set(d) - EVENT_TYPES[e.type] - EVENT_OPTIONAL.get(e.type, set())
        if extra:  # vocabulaire fermé AUSSI pour les champs : rien de libre ne part dans le jeu public
            err.append(f"{where} : {e.type} : champ(s) hors vocabulaire {sorted(extra)}")
        for k in BOOL_FIELDS & set(d):
            if not isinstance(d[k], bool):
                err.append(f"{where} : {k} n'est pas un booléen")
        if "action_id" in d and (isinstance(d["action_id"], bool) or not isinstance(d["action_id"], int)):
            err.append(f"{where} : action_id n'est pas un entier")
        if e.type == "robot_error_injected" and d.get("error_kind") not in ERROR_KINDS:
            err.append(f"{where} : error_kind {d.get('error_kind')!r} hors vocabulaire")
        if e.type == "source_event" and (e.source_code is None or e.source_code != e.source_code):
            err.append(f"{where} : source_event sans source_code")
        if e.episode_index not in bornes:
            err.append(f"{where} : épisode inconnu")
        else:
            a, b = bornes[e.episode_index]
            if not (a - TOL_DT <= e.t <= b + TOL_DT):
                err.append(f"{where} : hors de l'épisode [{a:.6f}, {b:.6f}]")
    n["événements"] = len(evt)
    if verifier_eeg:
        # « dans un trou » = entre deux échantillons REÇUS non adjacents (ou avant le 1er / après le dernier quand la
        # tête / la queue manquent). Encadrement dans les horodatages reçus, jamais round((t - t0) * fs) avec le fs
        # nominal : sous dérive tolérée, l'erreur vaut ppm × échantillons écoulés (KE#288).
        dans_trou = 0
        for e in evt.itertuples():
            if e.episode_index not in idx_par_ep:
                continue
            t_, idx_, i0, i1 = idx_par_ep[e.episode_index]
            if not len(t_):
                dans_trou += 1
                continue
            j = int(np.searchsorted(t_, e.t))  # premier échantillon reçu à t >= e.t
            if j < len(t_) and t_[j] == e.t:
                continue  # pile sur un échantillon reçu
            gauche = int(idx_[j - 1]) if j > 0 else i0 - 1
            droite = int(idx_[j]) if j < len(t_) else i1 + 1
            dans_trou += int(droite - gauche > 1)
        n["événements dans un trou"] = dans_trou
    n.update({f"  dont {k}": v for k, v in sorted(par_type.items())})
    return err, n


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    err, n = valider(Path(argv[1]))
    for k, v in n.items():
        print(f"{k} : {v}")
    for e in err:
        print("ERREUR", e)
    print("VALIDE" if not err else f"INVALIDE : {len(err)} erreur(s)")
    return 0 if not err else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
