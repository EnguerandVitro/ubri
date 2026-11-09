"""Tests UBRI-NRD. Lancer depuis products/ubri/nrd : .venv/bin/python -B tests/test_nrd.py [--bnci sorties/bnci2015013-nrd]

1. jeu synthétique valide (témoin positif exact : VALIDE, compteurs exacts), sans trou ET avec trous annoncés ;
2. des cassures du validateur (liste CASSURES), chacune doit rougir SUR SON PROPRE MOTIF (KE#139) ;
3. export public : jambe 1 seule, jambe 2 seule, chacune suffit à bloquer (KE#76, par jambe) ;
4. (--bnci) aller-retour exact contre les .mat d'origine, et export public BNCI = rien à publier.
"""
import atexit
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106 : relatif au fichier
from ubri_nrd import exporter as ex  # noqa: E402
from ubri_nrd.io import ecrire_eeg, ecrire_meta, lire_eeg, lire_eeg_grille, lire_table  # noqa: E402
from ubri_nrd.schema import EPISODE_COLUMNS, EVENT_COLUMNS, PARTICIPANT_COLUMNS  # noqa: E402
from ubri_nrd.valider import valider  # noqa: E402

FS = 100
CH = ["Fz", "FCz", "Cz"]


TROUS = (5, 20, 3)  # épisode 0 avec trous : 5 manquants en tête, 20 au milieu (idx 1090..1109, recouvre l'événement de l'échantillon 100), 3 en queue


def jeu(root: Path, consent=((True, True), (False, False)), licence="CC-BY-4.0", trous=False, annonce=True) -> Path:
    """2 participants, 1 épisode chacun, 3 événements par épisode. consent = (publication, usage commercial).
    trous=True : l'épisode 0 perd TROUS échantillons (tête, milieu, queue), annoncés par la source."""
    rng = np.random.default_rng(0)
    eps, evs = [], []
    for i, pid in enumerate(["sub-01", "sub-02"]):
        i0 = 1000 * (i + 1)  # le compteur de l'appareil ne part jamais de 0 : l'étendue est déclarée, pas supposée
        idx_tout = i0 + np.arange(200)
        t_tout = 10.0 * i + np.arange(200) / FS
        garde = np.ones(200, bool)
        if trous and i == 0:
            garde[:TROUS[0]] = False; garde[90:90 + TROUS[1]] = False; garde[200 - TROUS[2]:] = False
        idx, t = idx_tout[garde], t_tout[garde]
        rel = ecrire_eeg(root, i, t, rng.standard_normal((len(t), 3)), CH, idx=idx)
        perdus = int((~garde).sum())
        eps.append((i, pid, 1, 1, float(t_tout[0]), float(t_tout[-1]), len(t), rel, int(i0), int(i0 + 199),
                    (perdus if annonce else None) if trous else None))
        t = t_tout
        evs += [(float(t[50]), i, "robot_correct", json.dumps({"action_id": i}), 10),
                (float(t[100]), i, "robot_error_injected", json.dumps({"error_kind": "mauvais_objet", "planned": True}), 6),
                (float(t[150]), i, "source_event", "{}", 1)]
    part = pd.DataFrame([("sub-01", "30-39", "D", True, *consent[0], "test"),
                         ("sub-02", "40-49", "G", True, *consent[1], "test")], columns=PARTICIPANT_COLUMNS)
    info = {"agent": "robot", "eeg_device": "test", "fs": FS, "unit": "µV", "clock_sync": {"method": "test"},
            "source": "test", "licence": licence}
    ecrire_meta(root, info, {"channels": CH}, part, pd.DataFrame(eps, columns=EPISODE_COLUMNS),
                pd.DataFrame(evs, columns=EVENT_COLUMNS))
    return root


_CREES: list[Path] = []


def tmpdir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="nrd-test-"))
    _CREES.append(d)
    return d


@atexit.register
def _nettoyer():
    for d in _CREES:
        shutil.rmtree(d, ignore_errors=True)


def test_valide():
    r = jeu(tmpdir())
    err, n = valider(r)
    assert err == [], err
    assert n == {"participants": 2, "épisodes": 2, "fichiers EEG vérifiés": 2, "échantillons manquants": 0,
                 "trous (début, milieu, fin)": 0, "pertes non annoncées par la source (côté hôte)": 0,
                 "événements": 6, "événements dans un trou": 0,
                 "  dont robot_correct": 2, "  dont robot_error_injected": 2, "  dont source_event": 2}, n
    print("OK  jeu synthétique valide, compteurs exacts")
    r = jeu(tmpdir(), trous=True)
    err, n = valider(r)
    assert err == [], err
    assert (n["échantillons manquants"], n["trous (début, milieu, fin)"], n["événements dans un trou"]) == (28, 3, 1), n
    print("OK  jeu avec trous annoncés (5 en tête, 20 au milieu, 3 en queue) : valide ; 28 manquants, 3 trous, "
          "1 événement tombé dans un trou")
    r = jeu(tmpdir(), trous=True, annonce=False)  # source qui n'annonce rien : valide, manquants comptés, 0 « hôte »
    err, n = valider(r)
    assert err == [] and (n["échantillons manquants"], n["trous (début, milieu, fin)"],
                          n["pertes non annoncées par la source (côté hôte)"]) == (28, 3, 0), (err, n)
    print("OK  trous sans annonce de la source : valide, 28 manquants, 3 trous, 0 compté « côté hôte »")
    r = jeu(tmpdir())  # source qui annonce 0 alors que 10 manquent : jeu VALIDE, 10 comptés côté hôte (témoin exact)
    _eeg0(r, lambda d: d.drop(index=range(50, 60), inplace=True))
    _ep(r, lambda d: (d.__setitem__("n_samples", [190, 200]),
                      d.__setitem__("pertes_annoncees", pd.array([0, None], dtype="Int64"))))
    err, n = valider(r)
    assert err == [] and n["pertes non annoncées par la source (côté hôte)"] == 10, (err, n)
    print("OK  perte côté hôte (10 manquants, 0 annoncé) : jeu valide, 10 pertes non annoncées comptées et affichées")


def test_evenement_trou_sous_derive():
    """KE#288 : 100 Hz, 1 000 s, quartz à +500 ppm (toléré), trou idx 60000..60009. Un événement à l'heure de
    idx 60005 (dans le trou) compte 1 ; un événement sur idx 59975 (reçu) compte 0. Le fs nominal se trompait."""
    r = tmpdir()
    n_tot, pas = 100_000, (1 + 500e-6) / FS
    idx_tout = np.arange(n_tot, dtype=np.int64)
    garde = (idx_tout < 60000) | (idx_tout > 60009)
    t_tout = idx_tout * pas
    rel = ecrire_eeg(r, 0, t_tout[garde], np.zeros((int(garde.sum()), 3)), CH, idx=idx_tout[garde])
    eps = [(0, "sub-01", 1, 1, float(t_tout[0]), float(t_tout[-1]), int(garde.sum()), rel, 0, n_tot - 1, 10)]
    part = pd.DataFrame([("sub-01", "30-39", "D", True, True, True, "test")], columns=PARTICIPANT_COLUMNS)
    info = {"agent": "robot", "eeg_device": "test", "fs": FS, "unit": "µV", "clock_sync": {"method": "test"},
            "source": "test", "licence": "CC-BY-4.0"}
    for t_e, attendu in ((float(t_tout[60005]), 1), (float(t_tout[59975]), 0)):
        ecrire_meta(r, info, {"channels": CH}, part, pd.DataFrame(eps, columns=EPISODE_COLUMNS),
                    pd.DataFrame([(t_e, 0, "robot_correct", "{}", None)], columns=EVENT_COLUMNS))
        err, n = valider(r)
        assert err == [] and n["événements dans un trou"] == attendu, (t_e, attendu, err, n)
    print("OK  événement dans un trou sous dérive +500 ppm sur 1 000 s : 1 dans le trou, 0 sur un échantillon reçu")


def test_idx_obligatoire():
    try:
        ecrire_eeg(tmpdir(), 0, np.arange(3) / FS, np.zeros((3, 3)), CH)  # ancienne forme d'appel (KE#62)
    except TypeError as e:
        assert "idx" in str(e), e
        print("OK  ecrire_eeg sans idx : TypeError (aucun flux continu déclaré par omission)")
        return
    raise AssertionError("ecrire_eeg accepte encore l'appel sans idx")


def test_grille():
    r = jeu(tmpdir(), trous=True)
    ep = lire_table(r, "neural/episodes.parquet").iloc[0]
    idx, t, x, present = lire_eeg_grille(r, ep, CH)
    t_f, x_f = lire_eeg(r, ep.eeg_file, CH)
    assert len(idx) == 200 and idx[0] == 1000 and idx[-1] == 1199
    assert int((~present).sum()) == 28 and np.isnan(x[~present]).all()
    assert np.array_equal(x[present], x_f) and np.array_equal(t[present], t_f)  # présents rendus à l'identique
    assert np.allclose(t, np.arange(200) / FS, atol=1e-9)  # heures des manquants retrouvées par la droite t ~ idx
    print("OK  grille régulière : 200 points, 28 NaN aux bons endroits, présents identiques, heures des trous exactes")
    ep2 = ep.copy(); ep2["idx_start"] = ep.idx_start + 10  # le fichier contient des idx sous l'étendue : refus (KE#113)
    try:
        lire_eeg_grille(r, ep2, CH)
    except ValueError as e:
        assert "hors de" in str(e), e
        print("OK  grille : idx hors de l'étendue -> ValueError (jamais d'écriture en boucle par indice négatif)")
    else:
        raise AssertionError("lire_eeg_grille accepte des idx hors de l'étendue")


def _evt(r, f):
    e = lire_table(r, "neural/events.parquet"); res = f(e)
    e = res if isinstance(res, pd.DataFrame) else e
    ex.ecrire_parquet(r / "neural/events.parquet", e)


def _part(r, f):
    p = lire_table(r, "meta/participants.parquet"); res = f(p)
    p = res if isinstance(res, pd.DataFrame) else p
    ex.ecrire_parquet(r / "meta/participants.parquet", p)


def _eeg0(r, f):
    rel = "neural/eeg/chunk-000/file-000000.parquet"
    d = lire_table(r, rel); f(d); ex.ecrire_parquet(r / rel, d)


def _ep(r, f):
    e = lire_table(r, "neural/episodes.parquet"); res = f(e)
    e = res if isinstance(res, pd.DataFrame) else e
    ex.ecrire_parquet(r / "neural/episodes.parquet", e)


def _lien(r):
    f = r / "neural/eeg/chunk-000/file-000001.parquet"
    vrai = r / "ailleurs.parquet"
    f.rename(vrai)
    f.symlink_to(vrai)


def _vider(r):
    for f in (r / "neural/eeg").rglob("*.parquet"):
        f.unlink()
    _ep(r, lambda d: d.iloc[0:0])
    _evt(r, lambda e: e.iloc[0:0])


CASSURES = {
    "type hors vocabulaire": (lambda r: _evt(r, lambda e: e.__setitem__("type", e["type"].replace("robot_correct", "robot_ok"))),
                              "type 'robot_ok' hors vocabulaire"),
    "champ details hors vocabulaire": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].replace(
        '{"action_id": 0}', json.dumps({"action_id": 0, "nom_complet": "Jean Dupont"})))),
        "champ(s) hors vocabulaire ['nom_complet']"),
    "details pas un objet": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].replace("{}", "[]"))),
                             "n'est pas un objet JSON"),
    "planned pas booléen": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].str.replace(
        '"planned": true', '"planned": "oui"'))), "planned n'est pas un booléen"),
    "eeg_file d'un autre participant": (lambda r: _ep(r, lambda d: d.loc.__setitem__(
        (0, ["eeg_file", "t_start", "t_end"]), d.loc[1, ["eeg_file", "t_start", "t_end"]].to_numpy())), "attendu"),
    "EEG en lien symbolique": (lambda r: _lien(r), "lien symbolique"),
    "colonne EEG en trop": (lambda r: _eeg0(r, lambda d: d.__setitem__("nom", "x")), "attendu t + idx + montage"),
    "consent_collecte faux": (lambda r: _part(r, lambda p: p.__setitem__("consent_collecte", [True, False])),
                              "consent_collecte n'est pas True"),
    "jeu sans épisode": (lambda r: _vider(r), "aucun épisode"),
    "aucun participant": (lambda r: _part(r, lambda p: p.iloc[0:0]), "aucun participant"),
    "aucun participant (colonnes objet)": (lambda r: _part(r, lambda p: pd.DataFrame(columns=PARTICIPANT_COLUMNS)),
                                           "aucun participant"),
    "action_id pas entier": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].replace(
        '{"action_id": 0}', '{"action_id": "0"}'))), "action_id n'est pas un entier"),
    "champ facultatif d'un autre type": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].where(
        e["type"] != "source_event", json.dumps({"action_id": 3})))), "source_event : champ(s) hors vocabulaire"),
    "champ details manquant": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].replace(
        json.dumps({"error_kind": "mauvais_objet", "planned": True}), json.dumps({"planned": True})))), "sans ['error_kind']"),
    "error_kind inconnu": (lambda r: _evt(r, lambda e: e.__setitem__("details", e["details"].str.replace(
        "mauvais_objet", "bizarre"))), "error_kind 'bizarre'"),
    "événement hors épisode": (lambda r: _evt(r, lambda e: e.__setitem__("t", e["t"] + 100.0)), "hors de l'épisode"),
    "source_event sans code": (lambda r: _evt(r, lambda e: e.__setitem__("source_code", e["source_code"].where(
        e["type"] != "source_event", None))), "source_event sans source_code"),
    "horodatage non croissant": (lambda r: _eeg0(r, lambda d: d.__setitem__("t", d["t"][::-1].to_numpy())),
                                 "non strictement croissant"),
    "pas ≠ 1/fs": (lambda r: _eeg0(r, lambda d: d.__setitem__("t", d["t"] * 1.01)), "pas d'échantillonnage"),
    "consentement non booléen": (lambda r: _part(r, lambda p: p.__setitem__("consent_publication",
                                                                            pd.Series([True, None], dtype=object))),
                                 "consent_publication n'est pas un booléen"),
    "EEG manquant": (lambda r: (r / "neural/eeg/chunk-000/file-000001.parquet").unlink(), "EEG manquant"),
    "EEG orphelin": (lambda r: shutil.copyfile(r / "neural/eeg/chunk-000/file-000000.parquet",
                                               r / "neural/eeg/chunk-000/file-000099.parquet"), "EEG orphelin"),
    "info.json absent": (lambda r: (r / "meta/info.json").unlink(), "info.json absent"),
    "idx non croissant": (lambda r: _eeg0(r, lambda d: d.__setitem__("idx", d["idx"][::-1].to_numpy())),
                          "idx non strictement croissant"),
    "idx hors de l'étendue": (lambda r: _ep(r, lambda d: d.__setitem__("idx_end", d["idx_end"] - 10)),
                              "hors de [idx_start, idx_end]"),
    "annonce supérieure aux manquants": (lambda r: _ep(r, lambda d: d.__setitem__(
        "pertes_annoncees", pd.array([3, None], dtype="Int64"))), "< 3 pertes annoncées"),
    "idx uint64": (lambda r: _eeg0(r, lambda d: d.__setitem__("idx", d["idx"].astype("uint64"))), "idx de type uint64"),
    "étendue vide": (lambda r: _ep(r, lambda d: d.__setitem__("idx_end", pd.array([None, 1199 + 1000], dtype="Int64"))),
                     "idx_start / idx_end non entiers"),
    "heure incohérente à travers un trou": (lambda r: (_eeg0(r, lambda d: (d.drop(index=range(50, 60), inplace=True),
                                                                           d.__setitem__("t", d["t"].where(d["idx"] < 1060, d["t"] - 0.1)))),
                                                       _ep(r, lambda d: (d.__setitem__("n_samples", [190, 200]),
                                                                         d.__setitem__("t_end", d["t_end"].where(d.index != 0, d["t_end"] - 0.1))))),
                                            "trous compris"),
    "t_start incohérent": (lambda r: _ep(r, lambda d: d.__setitem__("t_start", d["t_start"] + 0.05)), "t_start"),
    "idx flottant": (lambda r: _eeg0(r, lambda d: d.__setitem__("idx", d["idx"].astype(float))), "idx de type float64"),
    "version 0.1": (lambda r: ex.ecrire_json(r / "meta/info.json", {"ubri_nrd": {**json.loads(
        (r / "meta/info.json").read_text())["ubri_nrd"], "version": "0.1"}}), "à reconvertir"),
}


def test_derive_toleree():
    r = jeu(tmpdir())
    _eeg0(r, lambda d: d.__setitem__("t", d["t"].iloc[0] + (d["t"] - d["t"].iloc[0]) / (1 + 500e-6)))
    _ep(r, lambda d: d.__setitem__("t_end", d["t_end"].where(d.index != 0, lire_table(
        r, "neural/eeg/chunk-000/file-000000.parquet")["t"].iloc[-1])))
    err, _ = valider(r)
    assert err == [], err
    print("OK  quartz à +500 ppm : valide (tolérance 1000 ppm)")


def test_cassures():
    for nom, (casse, motif) in CASSURES.items():
        r = jeu(tmpdir())
        casse(r)
        err, _ = valider(r)
        assert err, f"cassure « {nom} » : le validateur reste VERT"
        assert any(motif in e for e in err), f"cassure « {nom} » : rouge mais pas sur son motif {motif!r} : {err}"
        print(f"OK  cassure « {nom} » -> rouge sur « {motif} »")


def test_export():
    src = jeu(tmpdir())
    dest = tmpdir() / "public"
    r = ex.exporter_public(src, dest)
    assert r["statut"] == "exporte" and r["publiés"] == ["sub-01"] and r["refusés (consentement manquant)"] == ["sub-02"], r
    assert list(lire_table(dest, "meta/participants.parquet")["participant_id"]) == ["sub-01"]
    assert list(lire_table(dest, "neural/episodes.parquet")["participant_id"]) == ["sub-01"]
    assert set(lire_table(dest, "neural/events.parquet")["episode_index"]) == {0}
    assert valider(dest)[0] == []
    print("OK  export : seul sub-01 publié, sortie valide")

    # Jambe 1 cassée (tout le monde « consenti ») : la jambe 2 doit refuser, et rien ne doit être écrit.
    orig = ex._consenti
    ex._consenti = lambda v: True
    dest2 = tmpdir() / "public"
    try:
        ex.exporter_public(src, dest2)
        raise AssertionError("jambe 1 cassée : export accepté, la jambe 2 n'a rien vu")
    except ex.ExportRefuse as e:
        assert "sans consentement de publication" in str(e), e
    finally:
        ex._consenti = orig
    assert not dest2.exists() and not dest2.with_name("public.tmp").exists()
    print("OK  jambe 1 cassée -> jambe 2 refuse, rien d'écrit")

    # Jambe 2 cassée (contrôle de sortie neutralisé) : la jambe 1 seule doit encore filtrer.
    orig2 = ex.controle_sortie
    ex.controle_sortie = lambda d: None
    try:
        dest3 = tmpdir() / "public"
        ex.exporter_public(src, dest3)
        assert list(lire_table(dest3, "meta/participants.parquet")["participant_id"]) == ["sub-01"]
    finally:
        ex.controle_sortie = orig2
    print("OK  jambe 2 cassée -> jambe 1 filtre seule")

    # Personne n'a consenti : statut nommé, aucun dossier créé.
    src0 = jeu(tmpdir(), consent=((False, False), (False, False)))
    dest0 = tmpdir() / "public"
    r0 = ex.exporter_public(src0, dest0)
    assert r0["statut"] == "rien_a_publier" and not dest0.exists(), r0
    print("OK  aucun consentement -> rien_a_publier, aucun dossier")

    # Usage commercial (KE#283) : sous CC-BY, sub-02 a consenti à la publication mais PAS à l'usage commercial.
    srcc = jeu(tmpdir(), consent=((True, True), (True, False)), licence="CC-BY-4.0")
    rc = ex.exporter_public(srcc, tmpdir() / "public")
    assert rc["publiés"] == ["sub-01"] and rc["licence commerciale"] is True, rc
    srcn = jeu(tmpdir(), consent=((True, True), (True, False)), licence="CC-BY-NC-4.0")
    rn = ex.exporter_public(srcn, tmpdir() / "public")
    assert rn["publiés"] == ["sub-01", "sub-02"] and rn["licence commerciale"] is False, rn
    print("OK  CC-BY : sans consentement commercial refusé ; CC-BY-NC : publication seule suffit")
    orig3 = ex._licence_commerciale
    ex._licence_commerciale = lambda l: False  # jambe 1 cassée : croit la licence non commerciale
    try:
        ex.exporter_public(srcc, tmpdir() / "public")
        raise AssertionError("jambe 1 commerciale cassée : export accepté")
    except ex.ExportRefuse as e:
        assert "usage commercial" in str(e), e
    finally:
        ex._licence_commerciale = orig3
    print("OK  jambe 1 commerciale cassée -> jambe 2 refuse")
    orig2 = ex.controle_sortie
    ex.controle_sortie = lambda d: None
    try:
        d4 = tmpdir() / "public"
        ex.exporter_public(srcc, d4)
        assert list(lire_table(d4, "meta/participants.parquet")["participant_id"]) == ["sub-01"]
    finally:
        ex.controle_sortie = orig2
    print("OK  jambe 2 cassée -> jambe 1 filtre seule l'usage commercial")

    # Un .tmp laissé par un export interrompu n'est jamais supprimé à l'aveugle.
    d5 = tmpdir() / "public"
    d5.with_name("public.tmp").mkdir()
    try:
        ex.exporter_public(src, d5)
        raise AssertionError(".tmp préexistant : export accepté")
    except ex.ExportRefuse as e:
        assert "existe" in str(e), e
    assert d5.with_name("public.tmp").exists()
    print("OK  .tmp préexistant -> refus, dossier laissé intact")


def test_bnci(root: Path):
    import scipy.io as sio
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from convertir_bnci import DATA, EXPECTED, FS
    ep = lire_table(root, "neural/episodes.parquet")
    ev = lire_table(root, "neural/events.parquet")
    ch = json.loads((root / "meta/montage.json").read_text())["channels"]
    n = 0
    for s, k in EXPECTED:
        runs = np.atleast_1d(sio.loadmat(DATA / f"Subject0{s}_s{k}.mat", squeeze_me=True, struct_as_record=False)["run"])
        for ri, r in enumerate(runs, start=1):
            row = ep[(ep.participant_id == f"sub-{s:02d}") & (ep.session == k) & (ep.run == ri)]
            assert len(row) == 1, (s, k, ri)
            row = row.iloc[0]
            _, x = lire_eeg(root, row.eeg_file, ch)
            assert x.dtype == np.asarray(r.eeg).dtype and np.array_equal(x, r.eeg), f"EEG différent : {s} {k} {ri}"
            e = ev[ev.episode_index == row.episode_index]
            pos = np.rint((e["t"].to_numpy() - row.t_start) * FS).astype(np.int64) + 1
            assert np.array_equal(pos, np.atleast_1d(r.header.EVENT.POS)), f"POS différent : {s} {k} {ri}"
            assert np.array_equal(e["source_code"].to_numpy(), np.atleast_1d(r.header.EVENT.TYP)), f"TYP : {s} {k} {ri}"
            n += 1
    assert n == 120, n
    print(f"OK  BNCI : {n} blocs, EEG identique au bit près, positions et codes identiques")
    dest = tmpdir() / "public"
    r = ex.exporter_public(root, dest)
    assert r["statut"] == "rien_a_publier" and len(r["refusés (consentement manquant)"]) == 6 and not dest.exists(), r
    print("OK  BNCI : export public -> rien_a_publier (licence ND, 6 participants refusés)")


if __name__ == "__main__":
    test_valide()
    test_idx_obligatoire()
    test_grille()
    test_evenement_trou_sous_derive()
    test_derive_toleree()
    test_cassures()
    test_export()
    if "--bnci" in sys.argv:
        test_bnci(Path(sys.argv[sys.argv.index("--bnci") + 1]))
    print("TOUT VERT")
