"""Banc ErrP BNCI 2015-013. Lancer : .venv/bin/python -B bench.py [intra|loso|tout|device|calib|adapt|degrade|agreg]

intra : le cas CALIBRÉ (borne haute). Pour chaque sujet, entraînement session 1, test session 2.
loso  : le cas SANS CALIBRATION, la thèse UBRI. Test sur un sujet jamais vu (leave_one_subject_out, splits.py).
device : le cas SANS CALIBRATION avec seulement les électrodes que le device UBRI atteint (voir DEVICE).
calib : courbe de calibration. Modèle des autres sujets + les k PREMIERS essais du nouveau (ordre chronologique),
        test sur les essais du nouveau après K_MAX (le même ensemble pour tous les k).
adapt : adaptation au nouveau sujet sur le device (recentrage riemannien sans étiquettes, pondération, les deux).
degrade : device réaliste, entraînement PROPRE (5 autres, gel), sujet testé BRUITÉ (simulation d'électrodes sèches).
agreg : mêmes scores que degrade, agrégés sur n observations d'un même événement (même personne ou n personnes).
Chaque mode a un témoin négatif : mêmes plis, étiquettes d'entraînement mélangées -> doit tomber vers 0,50.
(degrade et agreg : seule la régression reçoit les étiquettes mélangées, xDAWN garde les vraies ; dispersion
mesurée par outils/temoin_agreg.py.)
Métriques : AUC et précision équilibrée (les erreurs ne font que ~20 % des essais : la précision brute ment).
"""
import sys

import numpy as np
from pyriemann.estimation import XdawnCovariances
from pyriemann.tangentspace import TangentSpace
from pyriemann.geometry.base import invsqrtm
from pyriemann.geometry.mean import mean_riemann
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline

from prep import OUT

SEED = 0
IZ = 27  # position de Iz dans header.Label (ordre Biosemi du jeu)
# Ordre des 64 colonnes de r.eeg = header.Label[:64] (la 65e étiquette est « Status », hors de eeg).
# Vérifié le 2026-11-08 : identique sur les 120 blocs des 12 fichiers.
LABELS = ("Fp1 AF7 AF3 F1 F3 F5 F7 FT7 FC5 FC3 FC1 C1 C3 C5 T7 TP7 CP5 CP3 CP1 P1 P3 P5 P7 P9 PO7 PO3 O1 Iz Oz POz "
          "Pz CPz Fpz Fp2 AF8 AF4 AFz Fz F2 F4 F6 F8 FT8 FC6 FC4 FC2 FCz Cz C2 C4 C6 T8 TP8 CP6 CP4 CP2 P2 P4 P6 P8 "
          "P10 PO8 PO4 O2").split()
DEVICE = {
    "device7": ["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4"],
    "mini3": ["Fz", "FCz", "Cz"],
}


def model():
    return make_pipeline(
        XdawnCovariances(nfilter=4, estimator="lwf"),
        TangentSpace(metric="riemann"),
        LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000),
    )


def score(X, y, tr, te, shuffle=False):
    ytr = y[tr].copy()
    if shuffle:
        np.random.default_rng(SEED).shuffle(ytr)
    clf = model().fit(X[tr], ytr)
    p = clf.predict_proba(X[te])[:, 1]
    return roc_auc_score(y[te], p), balanced_accuracy_score(y[te], (p >= 0.5).astype(int))


def report(name, folds, X, y, labels):
    print(f"\n== {name}")
    res = {"vrai": [], "témoin": []}
    for lab, (tr, te) in zip(labels, folds):
        assert len(set(np.unique(y[te]))) == 2, f"{lab} : une seule classe en test"
        a = score(X, y, tr, te); b = score(X, y, tr, te, shuffle=True)
        res["vrai"].append(a); res["témoin"].append(b)
        print(f"  {lab:10s} AUC {a[0]:.3f}  préc. équilibrée {a[1]:.3f}   | témoin mélangé AUC {b[0]:.3f}")
    for k, v in res.items():
        v = np.asarray(v)
        print(f"  moyenne {k:7s} AUC {v[:, 0].mean():.3f} ± {v[:, 0].std():.3f}  préc. équilibrée {v[:, 1].mean():.3f}")
    return res


def loso_folds(subj, subjects):
    from splits import leave_one_subject_out
    folds = leave_one_subject_out(subj)
    assert len(folds) == len(subjects)
    for s, (tr, te) in zip(subjects, folds):  # garde anti-fuite indépendante de splits.py
        assert set(subj[te].tolist()) == {s} and s not in set(subj[tr].tolist()), f"fuite sur le sujet {s}"
    return folds


def device_idx(chans):
    idx = [LABELS.index(c) for c in chans]
    assert len(set(idx)) == len(chans)
    return idx


def reref(XA, chans):
    """Re-référence à la moyenne des électrodes du device, puis Fz retiré (rang n-1)."""
    return np.delete(XA - XA.mean(axis=1, keepdims=True), chans.index("Fz"), axis=1)


def device_reel(X64, chans):
    """Variante B : électrodes re-référencées à leur propre moyenne, Fz retiré (rang n-1)."""
    return reref(X64[:, device_idx(chans)], chans)


NIVEAUX = (0.0, 0.5, 1.0, 2.0, 3.0)  # écart-type du bruit / écart-type du signal, par électrode
K_DEG = 50  # 3 min de calibration (3,5 s par essai)


def bruit(shape, sd, rng):
    """Bruit blanc filtré 1-10 Hz (la bande du banc), d'écart-type sd[c] par électrode (après filtrage)."""
    import mne
    n = rng.standard_normal(shape)
    n = mne.filter.filter_data(n, 64.0, 1.0, 10.0, verbose="ERROR")
    n /= n.std(axis=(0, 2), keepdims=True)
    return n * sd[None, :, None]


def degrade(X64, y, subj, subjects, quiet=False):
    """Le bruit est ajouté sur chaque électrode AVANT la re-référence, comme sur une vraie électrode, et seulement
    sur le sujet testé (calibration et test). L'entraînement des 5 autres reste propre."""
    chans = DEVICE["device7"]
    XA = X64[:, device_idx(chans)]
    sd = XA.std(axis=(0, 2))  # amplitude du signal par électrode, sur tout le jeu
    scen = [(f"bruit {lv:.1f}x", lv, None) for lv in NIVEAUX] + [("mauvais contact (1 él., 5x)", 0.0, 5.0)]
    meths = ("générique", "R (3 min)", "R+P (3 min)", "témoin")
    res = {(sc[0], m): [] for sc in scen for m in meths}
    probas = {(sc[0], m): [] for sc in scen for m in meths}  # par sujet : (p sur te, y[te])
    print(f"\n== DÉGRADATION device7 réaliste : entraînement propre, sujet testé bruité ; test = essais après le {K_MAX}e")
    for s in subjects:
        own = np.where(subj == s)[0]
        assert np.all(np.diff(own) == 1)
        others = np.where(subj != s)[0]
        te, cal = own[K_MAX:], own[:K_DEG]
        assert not set(cal) & set(te)
        Xo = reref(XA[others], chans)
        xc = XdawnCovariances(nfilter=4, estimator="lwf").fit(Xo, y[others])
        Co = xc.transform(Xo)
        Crec = np.empty_like(Co)
        for o in subjects:
            if o != s:
                io = np.where(subj[others] == o)[0]
                Crec[io] = recentre(Co[io], mean_riemann(Co[io]))
        w = np.concatenate([np.ones(len(others)), np.full(K_DEG, len(others) / (len(subjects) - 1) / K_DEG)])
        for name, lv, bad in scen:
            rng = np.random.default_rng(1000 * s + int(10 * lv) + (7 if bad else 0))
            Xs = XA[own].copy()
            if lv:
                Xs += bruit(Xs.shape, lv * sd, rng)
            if bad:
                c = rng.integers(len(chans))
                one = np.zeros_like(sd); one[c] = bad * sd[c]
                Xs += bruit(Xs.shape, one, rng)
            Cs = xc.transform(reref(Xs, chans))
            Cte, Ccal = Cs[K_MAX:], Cs[:K_DEG]
            M = mean_riemann(Ccal)
            def ev(Ctr, ytr, Ct, ww=None, shuffle=False):
                ytr = ytr.copy()
                if shuffle:
                    np.random.default_rng(SEED).shuffle(ytr)
                ts = TangentSpace(metric="riemann").fit(Ctr)
                lr = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
                lr.fit(ts.transform(Ctr), ytr, sample_weight=ww)
                return lr.predict_proba(ts.transform(Ct))[:, 1]
            out = {
                "générique": ev(Co, y[others], Cte),
                "témoin": ev(Co, y[others], Cte, shuffle=True),
                "R (3 min)": ev(Crec, y[others], recentre(Cte, M)),
                "R+P (3 min)": ev(np.concatenate([Crec, recentre(Ccal, M)]), np.concatenate([y[others], y[cal]]),
                                  recentre(Cte, M), w),
            }
            for m, pr in out.items():
                res[(name, m)].append(roc_auc_score(y[te], pr))
                probas[(name, m)].append((pr, y[te]))
    print("  " + f"{'scénario':28s} " + " | ".join(f"{m:>13s}" for m in meths))
    for name, _, _ in scen:
        print(f"  {name:28s} " + " | ".join(
            f"{np.mean(res[(name, m)]):.3f} ±{np.std(res[(name, m)]):.2f}" for m in meths))
    return res, probas


NS = (1, 2, 3, 5, 10)
N_EV = 2000  # événements tirés par classe


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def agreg_meme(pr, yy, n, rng):
    """Une personne, n observations : n essais DISTINCTS de la même classe, scores (logit) moyennés."""
    L = _logit(pr)
    ev_s, ev_y = [], []
    for c in (0, 1):
        pool = np.where(yy == c)[0]
        assert len(pool) >= n, f"classe {c} : {len(pool)} essais < n={n}"
        for _ in range(N_EV):
            ev_s.append(L[rng.choice(pool, n, replace=False)].mean()); ev_y.append(c)
    return roc_auc_score(ev_y, ev_s)


def agreg_personnes(par_sujet, n, rng):
    """n personnes DIFFÉRENTES voient le même événement : un essai de la même classe chez n sujets distincts,
    chacun noté par SON modèle inter-sujets (aucun ne l'a vu en entraînement)."""
    ev_s, ev_y = [], []
    for c in (0, 1):
        for _ in range(N_EV):
            who = rng.choice(len(par_sujet), n, replace=False)
            sc = []
            for i in who:
                pr, yy = par_sujet[i]
                sc.append(_logit(pr[rng.choice(np.where(yy == c)[0])]))
            ev_s.append(np.mean(sc)); ev_y.append(c)
    return roc_auc_score(ev_y, ev_s)


def agreg(X64, y, subj, subjects):
    _, probas = degrade(X64, y, subj, subjects)
    rng = np.random.default_rng(SEED)
    print(f"\n== AGRÉGATION sur n observations d'un même événement ({N_EV} événements par classe)")
    for sc in ("bruit 0.0x", "bruit 0.5x", "bruit 1.0x", "bruit 2.0x"):
        for m in ("générique", "R+P (3 min)", "témoin"):
            ps = probas[(sc, m)]
            meme = [np.mean([agreg_meme(pr, yy, n, rng) for pr, yy in ps]) for n in NS]
            pers = [agreg_personnes(ps, n, rng) if n <= len(ps) else np.nan for n in NS]
            print(f"  {sc:11s} {m:12s} même personne : " + " ".join(f"n={n}:{a:.3f}" for n, a in zip(NS, meme)))
            print(f"  {'':11s} {'':12s} n personnes   : " + " ".join(
                f"n={n}:{a:.3f}" if a == a else f"n={n}:  -  " for n, a in zip(NS, pers)))


KS = (0, 10, 25, 50, 100, 200)
K_MAX = 200


def calib(X, y, subj, subjects, nom):
    """Pour chaque sujet : 'mélange' (5 autres + k premiers essais du sujet) et 'sujet seul' (k essais).
    Les indices d'un sujet dans le cache sont chronologiques (prep.py : session 1 blocs 1..10 puis session 2)."""
    print(f"\n== CALIBRATION {nom} : test = essais du sujet après le {K_MAX}e, identiques pour tous les k")
    res = {(m, k): [] for m in ("mélange", "sujet seul", "témoin") for k in KS}
    for s in subjects:
        own = np.where(subj == s)[0]
        assert np.all(np.diff(own) == 1), f"sujet {s} : essais non contigus, ordre chronologique non garanti"
        others = np.where(subj != s)[0]
        te = own[K_MAX:]
        assert len(set(np.unique(y[te]))) == 2
        for k in KS:
            cal = own[:k]
            assert not set(cal) & set(te)  # anti-fuite : calibration et test disjoints
            res[("mélange", k)].append(score(X, y, np.concatenate([others, cal]), te))
            res[("témoin", k)].append(score(X, y, np.concatenate([others, cal]), te, shuffle=True))
            if k and len(set(np.unique(y[cal]))) == 2:
                res[("sujet seul", k)].append(score(X, y, cal, te))
    print(f"  {'k':>4s} | {'mélange AUC (préc.)':>22s} | {'sujet seul AUC (n sujets)':>26s} | témoin")
    for k in KS:
        m = np.asarray(res[("mélange", k)]); t = np.asarray(res[("témoin", k)]); o = res[("sujet seul", k)]
        so = f"{np.mean([a for a, _ in o]):.3f} ({len(o)}/6)" if o else "-"
        print(f"  {k:4d} | {m[:, 0].mean():.3f} ± {m[:, 0].std():.3f} ({m[:, 1].mean():.3f}) |"
              f" {so:>26s} | {t[:, 0].mean():.3f}")
    return res


def recentre(C, M):
    """Ramène les covariances C au centre M : M^-1/2 C M^-1/2 (Zanini et al. 2018)."""
    W = invsqrtm(M)
    return W @ C @ W


def adapt(X, y, subj, subjects, nom):
    """Pour chaque sujet testé, xDAWN est appris sur les 5 autres (étiquettes des autres seulement).
    R   : chaque sujet d'entraînement recentré sur la moyenne de TOUS ses essais (sans étiquettes) ; le sujet testé
          sur la moyenne de ses k premiers essais (sans étiquettes).
    P   : les k premiers essais étiquetés ajoutés à la régression, poids total = celui d'un sujet moyen du groupe.
    R+P : les deux. 'borne' : R avec la moyenne de TOUS les essais du sujet testé (futurs compris, irréaliste).
    Test = essais du sujet après K_MAX, identiques pour tous les k et toutes les méthodes."""
    print(f"\n== ADAPTATION {nom} : test = essais du sujet après le {K_MAX}e")
    meths = ("générique", "P", "R", "R+P", "témoin R+P")
    res = {(m, k): [] for m in meths for k in KS}
    borne = []

    def fit_eval(Ctr, ytr, Cte, w=None, shuffle=False):
        ytr = ytr.copy()
        if shuffle:
            np.random.default_rng(SEED).shuffle(ytr)
        ts = TangentSpace(metric="riemann").fit(Ctr)
        lr = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
        lr.fit(ts.transform(Ctr), ytr, sample_weight=w)
        p = lr.predict_proba(ts.transform(Cte))[:, 1]
        return p

    for s in subjects:
        own = np.where(subj == s)[0]
        assert np.all(np.diff(own) == 1)
        others = np.where(subj != s)[0]
        te = own[K_MAX:]
        xc = XdawnCovariances(nfilter=4, estimator="lwf").fit(X[others], y[others])
        C = xc.transform(X)
        Crec_others = np.empty_like(C[others])
        for o in subjects:
            if o == s:
                continue
            io = np.where(subj[others] == o)[0]
            Crec_others[io] = recentre(C[others][io], mean_riemann(C[others][io]))
        n_per_subject = len(others) / (len(subjects) - 1)
        mtr = lambda p: (roc_auc_score(y[te], p), balanced_accuracy_score(y[te], (p >= 0.5).astype(int)))
        borne.append(mtr(fit_eval(Crec_others, y[others], recentre(C[te], mean_riemann(C[own])))))
        res[("générique", 0)].append(mtr(fit_eval(C[others], y[others], C[te])))  # ne dépend pas de k
        for k in KS[1:]:
            cal = own[:k]
            assert not set(cal) & set(te)
            assert k >= 2  # moyenne riemannienne de R : au moins 2 essais
            w = np.concatenate([np.ones(len(others)), np.full(k, n_per_subject / k)])
            res[("P", k)].append(mtr(fit_eval(np.concatenate([C[others], C[cal]]), np.concatenate([y[others], y[cal]]),
                                              C[te], w)))
            M = mean_riemann(C[cal])
            Cte_r, Ccal_r = recentre(C[te], M), recentre(C[cal], M)
            res[("R", k)].append(mtr(fit_eval(Crec_others, y[others], Cte_r)))
            Ctr = np.concatenate([Crec_others, Ccal_r]); ytr = np.concatenate([y[others], y[cal]])
            res[("R+P", k)].append(mtr(fit_eval(Ctr, ytr, Cte_r, w)))
            res[("témoin R+P", k)].append(mtr(fit_eval(Ctr, ytr, Cte_r, w, shuffle=True)))
    print("  " + f"{'k':>4s} " + " | ".join(f"{m:>13s}" for m in meths))
    for k in KS:
        cells = []
        for m in meths:
            v = np.asarray(res[(m, k)])
            cells.append(f"{v[:, 0].mean():.3f} ±{v[:, 0].std():.2f}" if len(v) else f"{'-':>13s}")
        print(f"  {k:4d} " + " | ".join(f"{c:>13s}" for c in cells))
    b = np.asarray(borne)
    print(f"  borne R (moyenne sur tous les essais du sujet, irréaliste) : AUC {b[:, 0].mean():.3f} ± {b[:, 0].std():.2f}")
    return res


def device(X64, y, subj, subjects):
    """X64 : les 64 canaux en référence moyenne (cache). Deux variantes par jeu d'électrodes :
    A (optimiste) : sous-ensemble des données référencées sur 64 canaux, que le device ne peut pas calculer.
    B (réaliste)  : sous-ensemble re-référencé à SA propre moyenne (linéaire : identique à re-référencer le brut),
                    puis un canal retiré (rang n-1, même raison que Iz), en gardant FCz."""
    assert len(LABELS) == 64 and LABELS[IZ] == "Iz"
    folds = loso_folds(subj, subjects)
    labs = [f"sujet {s}" for s in subjects]
    out = {}
    for name, chans in DEVICE.items():
        idx = [LABELS.index(c) for c in chans]
        assert len(set(idx)) == len(chans)
        XA = X64[:, idx]
        XB = device_reel(X64, chans)  # même définition que calib et adapt
        out[name + " A"] = report(f"DEVICE {name} {chans} A : référence 64 canaux (optimiste)", folds, XA, y, labs)
        out[name + " B"] = report(f"DEVICE {name} {chans} B : référence propre, Fz retiré (réaliste)", folds, XB, y, labs)
    return out


def main(mode: str) -> None:
    d = np.load(OUT)
    X, y, subj, sess = d["X"].astype(np.float64), d["y"].astype(int), d["subject"], d["session"]
    subjects = sorted(set(subj.tolist()))
    assert subjects == [1, 2, 3, 4, 5, 6], subjects  # couverture (KE#111)
    if mode == "device":
        device(X, y, subj, subjects)
        return
    if mode == "degrade":
        degrade(X, y, subj, subjects)
        return
    if mode == "agreg":
        agreg(X, y, subj, subjects)
        return
    if mode == "adapt":
        adapt(device_reel(X, DEVICE["device7"]), y, subj, subjects, "device7 réaliste")
        return
    if mode == "calib":
        calib(device_reel(X, DEVICE["device7"]), y, subj, subjects, "device7 réaliste")
        calib(np.delete(X, IZ, axis=1), y, subj, subjects, "64 électrodes (Iz retiré)")
        return
    # La référence moyenne rend les 64 canaux de rang 63 (vp min ~1e-13, mesuré 2026-11-08) : la covariance
    # d'xDAWN n'est pas inversible (LinAlgError au pli inter-sujets 2). Un canal retiré ne perd aucune
    # information (il est la somme des 63 autres, au signe près). On retire Iz (indice 27, nuque), loin de FCz.
    assert X.shape[1] == 64, X.shape
    X = np.delete(X, IZ, axis=1)

    if mode in ("intra", "tout"):
        folds = [(np.where((subj == s) & (sess == 1))[0], np.where((subj == s) & (sess == 2))[0]) for s in subjects]
        report("INTRA-SUJET (calibré) : session 1 -> session 2", folds, X, y, [f"sujet {s}" for s in subjects])

    if mode in ("loso", "tout"):
        folds = loso_folds(subj, subjects)
        report("INTER-SUJETS (sans calibration) : sujet jamais vu", folds, X, y, [f"sujet {s}" for s in subjects])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "intra")
