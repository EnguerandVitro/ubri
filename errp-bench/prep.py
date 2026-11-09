"""Prétraitement BNCI 2015-013 (Chavarriaga & Millán 2010) -> époques ErrP en cache.

Codes d'événement (description.pdf officielle, bits 0..3 = cible G, cible D, curseur G, curseur D) :
  5 = 0101 cible G + curseur G -> correct      10 = 1010 cible D + curseur D -> correct
  6 = 0110 cible D + curseur G -> ERREUR        9 = 1001 cible G + curseur D -> ERREUR
Les autres codes (1, 2, 4, 8) ne sont pas des mouvements jugeables : ignorés.

Chaîne : référence moyenne commune, passe-bande 1-10 Hz (bande classique de l'ErrP), époque 0-800 ms
après le mouvement, décimation 512 -> 64 Hz. Sortie : data/epochs.npz (X, y, subject, session, run).
Lancer : .venv/bin/python -B prep.py
"""
import os
from pathlib import Path

import mne
import numpy as np
import scipy.io as sio

ROOT = Path(os.environ.get("UBRI_ERRP_ROOT", Path(__file__).resolve().parent))
DATA = ROOT / "data"
OUT = DATA / "epochs.npz"

CORRECT = {5, 10}
ERROR = {6, 9}
SFREQ = 512
TMIN_S, TMAX_S = 0.0, 0.8
DECIM = 8  # 512 -> 64 Hz ; sûr après un passe-bas à 10 Hz


def label_of(code: int) -> int | None:
    """1 = erreur, 0 = correct, None = pas un mouvement jugeable."""
    if code in ERROR:
        return 1
    if code in CORRECT:
        return 0
    return None


def epochs_of_run(eeg: np.ndarray, pos: np.ndarray, typ: np.ndarray):
    """eeg (n_samples, 64) -> X (n_trials, 64, n_times), y (n_trials,)."""
    x = eeg.T.astype(np.float64)
    x -= x.mean(axis=0, keepdims=True)  # référence moyenne commune
    x = mne.filter.filter_data(x, SFREQ, 1.0, 10.0, verbose="ERROR")
    a, b = int(TMIN_S * SFREQ), int(TMAX_S * SFREQ)
    X, y = [], []
    for p, t in zip(np.atleast_1d(pos), np.atleast_1d(typ)):
        lab = label_of(int(t))
        if lab is None:
            continue
        s = int(p) - 1  # positions MATLAB, base 1
        if s + a < 0 or s + b > x.shape[1]:
            continue
        X.append(x[:, s + a : s + b : DECIM])
        y.append(lab)
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.int8)


def main() -> None:
    Xs, ys, subj, sess, runs = [], [], [], [], []
    expected = [(s, k) for s in range(1, 7) for k in (1, 2)]
    for s, k in expected:
        f = DATA / f"Subject0{s}_s{k}.mat"
        m = sio.loadmat(f, squeeze_me=True, struct_as_record=False)
        for ri, r in enumerate(np.atleast_1d(m["run"]), start=1):
            h = r.header
            assert int(h.Subject) == s and int(h.Session) == k, f"{f.name} run {ri} : en-tête incohérent"
            assert int(h.SampleRate) == SFREQ, f"{f.name} : fréquence {h.SampleRate}"
            assert r.eeg.shape[1] == 64, f"{f.name} : {r.eeg.shape[1]} canaux"
            X, y = epochs_of_run(r.eeg, h.EVENT.POS, h.EVENT.TYP)
            Xs.append(X); ys.append(y)
            subj += [s] * len(y); sess += [k] * len(y); runs += [ri] * len(y)
        n_err = int(sum(y.sum() for y in ys[-10:])); n = sum(len(y) for y in ys[-10:])
        print(f"sujet {s} session {k} : {n} essais, {n_err} erreurs ({100 * n_err / n:.1f} %)")
    X = np.concatenate(Xs); y = np.concatenate(ys)
    subj = np.asarray(subj, dtype=np.int8); sess = np.asarray(sess, dtype=np.int8); runs = np.asarray(runs, dtype=np.int8)
    # Couverture (KE#111) : les 12 fichiers et les 6 sujets doivent être présents, avec des erreurs partout.
    assert sorted(set(zip(subj.tolist(), sess.tolist()))) == expected, "fichier ou session manquant"
    for s, k in expected:
        rate = y[(subj == s) & (sess == k)].mean()
        assert 0.10 < rate < 0.35, f"sujet {s} s{k} : taux d'erreur {rate:.2f} hors de l'ordre attendu (~0,20)"
    tmp = OUT.with_suffix(".tmp.npz")
    np.savez(tmp, X=X, y=y, subject=subj, session=sess, run=runs, sfreq=SFREQ // DECIM)
    os.replace(tmp, OUT)  # écriture atomique (KE#112)
    print(f"OK {X.shape} -> {OUT} ; erreurs {100 * y.mean():.1f} %")


if __name__ == "__main__":
    main()
