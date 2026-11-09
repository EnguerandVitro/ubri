"""Banc « artefacts réalistes + nettoyage ciblé » (DEV-13). Lancer : .venv/bin/python -B artefacts.py

Question testée : un nettoyage qui exploite les propriétés connues des artefacts (forme, spectre, topographie)
rend-il l'AUC d'un device à 7 électrodes ? Remplace le bruit blanc du mode degrade par trois artefacts SIMULÉS :
  - clignements : bosse lente (sigma 80-130 ms), 80-200 µV à Fp1, propagée vers l'arrière (Fz 0,45 ... Pz 0,08),
    processus de Poisson 0,3 /s ;
  - sauts d'électrode (mouvement, contact sec) : marche à décroissance exponentielle (tau 0,2-0,6 s), 30-100 µV, sur
    UNE électrode, 0,05 /s par électrode ;
  - bouffées musculaires (mâchoire, tempes) : bruit 15-150 Hz simulé à 512 Hz, 10-30 µV efficaces, surtout C3/C4,
    0,3 /s, puis le MÊME passe-bande 1-10 Hz que prep.py.
Chaque artefact est généré sur une fenêtre élargie (le filtrage est continu dans un vrai enregistrement), filtré
1-10 Hz, puis découpé sur l'époque 0-800 ms. Amplitudes et taux : ordres de grandeur de la littérature, CHOISIS
(simulé, pas mesuré) : le chiffre réel viendra du prototype.

Méthodes, toutes SANS étiquettes du sujet testé, paramètres appris sur ses K_MAX premiers essais (jamais testés) :
  aucun   : la chaîne du device telle quelle ;
  rejet   : époque écartée si une électrode dépasse SEUIL × son amplitude crête-à-crête médiane ;
  ciblé   : (1) saut isolé sur une électrode -> électrode reconstruite par régression sur les 6 autres (apprise sur
            les époques propres) ; (2) clignements -> topographie estimée sur les époques détectées, composante
            retirée (projection spatiale, appliquée aussi à l'entraînement : même espace) ;
  ciblé+rejet : les deux.
Scénario piège « clignements liés aux erreurs » : on cligne plus après une erreur, sur TOUS les sujets (entraînement
compris). Le classifieur peut alors lire le clignement au lieu de l'ErrP : une AUC qui MONTE est un faux succès.
Contrôles : témoin (chaîne ENTIÈRE, xDAWN compris, sur étiquettes mélangées, une graine) ; taux de détection mesuré contre la VÉRITÉ des artefacts injectés.
"""
import sys

import mne
import numpy as np
from pyriemann.estimation import XdawnCovariances
from pyriemann.tangentspace import TangentSpace
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline

from bench import DEVICE, K_MAX, SEED, device_idx, reref
from prep import OUT

CH = DEVICE["device7"]  # Fz FCz Cz CPz Pz C3 C4
FS, FS_BRUT = 64.0, 512.0
N_T = 52  # échantillons d'une époque (0-800 ms à 64 Hz)
PAD_S = 4.0  # marge de part et d'autre (s) : le passe-haut à 1 Hz a besoin de ~3 s
W_CLIGNE = np.array([0.45, 0.30, 0.18, 0.12, 0.08, 0.15, 0.15])  # fraction de l'amplitude à Fp1
W_EMG = np.array([0.3, 0.2, 0.2, 0.2, 0.3, 1.0, 1.0])
SEUIL = 3.0  # × crête-à-crête médiane de l'électrode
MIN_CLIGNES = 5  # époques détectées requises pour estimer une topographie


def _filtre(x, fs):
    return mne.filter.filter_data(x, fs, 1.0, 10.0, verbose="ERROR")


def _poisson(rng, taux, duree):
    return rng.uniform(0, duree, rng.poisson(taux * duree))


def clignements(n, rng, taux=0.3, extra_si=None, p_extra=0.0):
    """(n, 7, N_T) en µV + vérité (pic du clignement à Fz dans l'époque). extra_si : masque d'époques où un
    clignement supplémentaire suit l'événement avec la probabilité p_extra (200-600 ms)."""
    pad = int(PAD_S * FS); L = N_T + 2 * pad
    t = (np.arange(L) - pad) / FS
    A = np.zeros((n, L))
    for i in range(n):
        tcs = list(_poisson(rng, taux, L / FS) - PAD_S)
        if extra_si is not None and extra_si[i] and rng.random() < p_extra:
            tcs.append(rng.uniform(0.2, 0.6))
        for tc in tcs:
            A[i] += rng.uniform(80, 200) * np.exp(-(t - tc) ** 2 / (2 * rng.uniform(0.08, 0.13) ** 2))
    A = _filtre(A, FS)[:, pad:pad + N_T]
    return A[:, None, :] * W_CLIGNE[None, :, None], np.abs(A * W_CLIGNE[0]).max(axis=1)


def sauts(n, rng, taux=0.05):
    pad = int(PAD_S * FS); L = N_T + 2 * pad
    t = (np.arange(L) - pad) / FS
    A = np.zeros((n, len(CH), L))
    for i in range(n):
        for c in range(len(CH)):
            for t0 in _poisson(rng, taux, L / FS) - PAD_S:
                a = rng.uniform(30, 100) * rng.choice((-1, 1))
                A[i, c] += np.where(t >= t0, a * np.exp(-(t - t0) / rng.uniform(0.2, 0.6)), 0.0)
    A = _filtre(A.reshape(-1, L), FS).reshape(n, len(CH), L)[:, :, pad:pad + N_T]
    return A, np.abs(A).max(axis=2)  # vérité : amplitude par électrode dans l'époque


def muscle(n, rng, taux=0.3, bas=15.0, filtre_final=True):
    pad = int(PAD_S * FS_BRUT); n_b = int(0.8 * FS_BRUT); L = n_b + 2 * pad
    t = (np.arange(L) - pad) / FS_BRUT
    out = np.zeros((n, len(CH), N_T))
    for a in range(0, n, 100):  # par paquets de 100 époques
        m = min(100, n - a)
        env = np.zeros((m, L))
        for i in range(m):
            for tc in _poisson(rng, taux, L / FS_BRUT) - 2.0:
                d = rng.uniform(0.3, 1.0)
                env[i] += rng.uniform(10, 30) * np.where(np.abs(t - tc) < d / 2, np.cos(np.pi * (t - tc) / d) ** 2, 0)
        z = mne.filter.filter_data(rng.standard_normal((m * len(CH), L)), FS_BRUT, bas, 150.0, verbose="ERROR")
        z = z.reshape(m, len(CH), L) / z.std()
        x = z * env[:, None, :] * W_EMG[None, :, None]
        if filtre_final:
            x = _filtre(x.reshape(-1, L), FS_BRUT).reshape(m, len(CH), L)
        out[a:a + m] = x[:, :, pad:pad + n_b:8][:, :, :N_T]
    return out


# ---------------------------------------------------------------- nettoyage (sans étiquettes)
def reref6(X7):
    """La chaîne du device (bench.reref) : moyenne des 7 électrodes retirée, Fz enlevé (rang 6)."""
    return reref(X7, CH)


class Nettoyeur:
    """Appris sur des époques SANS étiquettes (les K_MAX premières d'un sujet), en coordonnées d'électrodes."""

    def __init__(self, Xcal7):
        self.med = np.median(np.ptp(Xcal7, axis=2), axis=0)
        r = np.ptp(Xcal7, axis=2) / self.med
        propre = Xcal7[(r < 2.0).all(axis=1)]
        assert len(propre) >= 20, f"{len(propre)} époques propres seulement pour apprendre la reconstruction"
        P = propre.transpose(1, 0, 2).reshape(len(CH), -1)
        self.B = {}
        for c in range(len(CH)):
            autres = [k for k in range(len(CH)) if k != c]
            self.B[c] = (autres, np.linalg.lstsq(P[autres].T, P[c], rcond=None)[0])

    def ratios(self, X7):
        return np.ptp(X7, axis=2) / self.med

    def sauts(self, X7):
        """Électrode isolée hors seuil -> masque (n,) et indice de l'électrode."""
        r = self.ratios(X7)
        tri = np.sort(r, axis=1)
        isole = (tri[:, -1] > SEUIL) & (tri[:, -1] > 2 * tri[:, -2])
        return isole, r.argmax(axis=1)

    def clignes(self, X7):
        """Clignement : Fz et FCz hors seuil ensemble, gradient avant -> arrière (Fz > Cz > Pz en crête-à-crête)."""
        r, p = self.ratios(X7), np.ptp(X7, axis=2)
        fz, fcz, cz, pz = (CH.index(c) for c in ("Fz", "FCz", "Cz", "Pz"))
        return (r[:, fz] > SEUIL) & (r[:, fcz] > SEUIL / 2) & (p[:, fz] > p[:, cz]) & (p[:, cz] > p[:, pz])

    def reconstruire(self, X7):
        X7 = X7.copy()
        isole, c = self.sauts(X7)
        for i in np.where(isole)[0]:
            autres, b = self.B[int(c[i])]
            X7[i, c[i]] = b @ X7[i, autres]
        return X7, isole


def topographie(X7, masque):
    """Vecteur spatial dominant des clignements détectés (7 électrodes), au pic de Fz de chaque époque."""
    fz = CH.index("Fz")
    V = np.array([X7[i, :, np.abs(X7[i, fz]).argmax()] for i in np.where(masque)[0]])
    V *= np.sign(V[:, fz])[:, None]
    return np.linalg.svd(V, full_matrices=False)[2][0]


def projeteur(u7):
    """Base de l'orthogonal de la topographie, dans l'espace re-référencé à 6 voies (rang plein : 5 voies)."""
    u6 = reref6(u7[None, :, None])[0, :, 0]
    u6 /= np.linalg.norm(u6)
    Q = np.linalg.svd(np.eye(6) - np.outer(u6, u6))[0][:, :5]
    return Q.T


# ---------------------------------------------------------------- banc
def modele():
    return make_pipeline(XdawnCovariances(nfilter=4, estimator="lwf"), TangentSpace(metric="riemann"),
                         LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000))


def auc(Xtr, ytr, Xte, yte, shuffle=False):
    ytr = ytr.copy()
    if shuffle:
        np.random.default_rng(SEED).shuffle(ytr)
    return roc_auc_score(yte, modele().fit(Xtr, ytr).predict_proba(Xte)[:, 1])


def diagnostics(XA):
    """Grandeurs citées par la note (KE#129) : produites ici, jamais mesurées à la main."""
    rng = np.random.default_rng(99)
    fond = XA.std(axis=(0, 2))
    print("  EEG de fond (écart-type par électrode, µV) : " + " ".join(f"{c} {v:.1f}" for c, v in zip(CH, fond)))
    _, vc = clignements(2000, rng)
    print(f"  clignements : {np.mean(vc > 15):.0%} des époques > 15 µV à Fz ; décile haut > {np.quantile(vc, 0.9):.0f} µV")
    _, vs = sauts(2000, rng)
    print(f"  sauts : {np.mean((vs > 25).sum(1) == 1):.0%} des époques avec un saut > 25 µV sur une seule électrode")
    c3 = CH.index("C3")
    for bas in (15.0, 10.0, 5.0):
        brut = muscle(300, np.random.default_rng(7), bas=bas, filtre_final=False)[:, c3].std()
        reste = muscle(300, np.random.default_rng(7), bas=bas)[:, c3].std()
        print(f"  muscle dès {bas:4.1f} Hz, C3 : {brut:.2f} µV efficaces injectés, {reste:.2f} µV après 1-10 Hz "
              f"({reste / fond[c3]:.2f} × le fond)")


SCEN = ("propre", "clignements", "sauts", "muscle", "mélange", "clignements liés aux erreurs (piège)")


def ajouter(nom, Xs7, ys, rng):
    """Artefacts du scénario sur les époques Xs7 d'UN sujet. Renvoie (X, vérité clignement, vérité saut)."""
    n = len(Xs7)
    vc, vs = np.zeros(n), np.zeros((n, len(CH)))
    X = Xs7.copy()
    if nom in ("clignements", "mélange"):
        a, vc = clignements(n, rng); X += a
    if nom == "clignements liés aux erreurs (piège)":
        a, vc = clignements(n, rng, taux=0.2, extra_si=ys == 1, p_extra=0.6); X += a
    if nom in ("sauts", "mélange"):
        a, vs = sauts(n, rng); X += a
    if nom in ("muscle", "mélange"):
        X += muscle(n, rng)
    return X, vc, vs


def main() -> int:
    d = np.load(OUT)
    X64, y, subj = d["X"].astype(np.float64), d["y"].astype(int), d["subject"]
    subjects = sorted(set(subj.tolist()))
    assert subjects == [1, 2, 3, 4, 5, 6], subjects  # couverture (KE#111)
    XA = X64[:, device_idx(CH)]
    meths = ("aucun", "rejet", "ciblé", "ciblé+rejet", "témoin")
    res = {(s, m): [] for s in SCEN for m in meths}
    proj_par_sujet = {s: [] for s in SCEN}
    garde = {s: [] for s in SCEN}  # fraction d'époques gardées par le rejet
    det = {s: {"cl_vrai": 0, "cl_vu": 0, "cl_juste": 0, "sa_vrai": 0, "sa_vu": 0, "sa_juste": 0} for s in SCEN}
    print("== DIAGNOSTICS des artefacts simulés")
    diagnostics(XA)
    print("\n== ARTEFACTS RÉALISTES (simulés) sur device7 réaliste, sujet jamais vu ; test = essais après le "
          f"{K_MAX}e ; paramètres de nettoyage appris sur les {K_MAX} premiers SANS étiquettes")
    for si, nom in enumerate(SCEN):
        piege = nom.startswith("clignements liés")
        # Artefacts : sur le sujet testé seulement, sauf le piège (tous les sujets, entraînement compris).
        Xart = XA.copy(); VC = np.zeros(len(y)); VS = np.zeros((len(y), len(CH)))
        for s in subjects:
            rng = np.random.default_rng(10_000 * si + s)
            own = np.where(subj == s)[0]
            if nom != "propre":
                Xart[own], VC[own], VS[own] = ajouter(nom, XA[own], y[own], rng)
        for s in subjects:
            own = np.where(subj == s)[0]
            assert np.all(np.diff(own) == 1)
            cal, te = own[:K_MAX], own[K_MAX:]
            others = np.where(subj != s)[0]
            Xtr7 = Xart[others] if piege else XA[others]  # entraînement : gel propre (sauf piège)
            Xte7 = Xart[te]
            yte = y[te]
            # aucun / rejet
            Xtr6, Xte6 = reref6(Xtr7), reref6(Xte7)
            clf = modele().fit(Xtr6, y[others])
            p = clf.predict_proba(Xte6)[:, 1]
            res[(nom, "aucun")].append(roc_auc_score(yte, p))
            net = Nettoyeur(Xart[cal])
            ok = (net.ratios(Xte7) <= SEUIL).all(axis=1)
            res[(nom, "rejet")].append(roc_auc_score(yte[ok], p[ok]))
            garde[nom].append(ok.mean())
            # ciblé : reconstruction des sauts, puis projection des clignements (topographie apprise sur cal)
            Xcal_r, _ = net.reconstruire(Xart[cal])
            mcal = net.clignes(Xcal_r)
            Xte_r, isole = net.reconstruire(Xte7)
            if piege:  # l'entraînement porte aussi des artefacts : chaque sujet nettoyé avec SES paramètres
                Xtr_r = np.empty_like(Xtr7)
                for o in subjects:
                    if o != s:
                        io = np.where(subj[others] == o)[0]
                        Xtr_r[io] = Nettoyeur(Xtr7[io[:K_MAX]]).reconstruire(Xtr7[io])[0]
            else:
                Xtr_r = Xtr7
            proj = bool(mcal.sum() >= MIN_CLIGNES)
            proj_par_sujet[nom].append(proj)
            if proj:
                Q = projeteur(topographie(Xcal_r, mcal))
                Xtr_c = np.einsum("kc,nct->nkt", Q, reref6(Xtr_r))
                Xte_c = np.einsum("kc,nct->nkt", Q, reref6(Xte_r))
            else:
                Xtr_c, Xte_c = reref6(Xtr_r), reref6(Xte_r)
            clf = modele().fit(Xtr_c, y[others])
            pc = clf.predict_proba(Xte_c)[:, 1]
            res[(nom, "ciblé")].append(roc_auc_score(yte, pc))
            okc = (net.ratios(Xte_r) <= SEUIL).all(axis=1) | (net.clignes(Xte_r) & proj)  # gardé SEULEMENT si retiré
            res[(nom, "ciblé+rejet")].append(roc_auc_score(yte[okc], pc[okc]))
            res[(nom, "témoin")].append(auc(Xtr_c, y[others], Xte_c, yte, shuffle=True))
            # détection contre la vérité injectée (sur le test)
            vrai_c = VC[te] > 15.0  # clignement visible : > 15 µV à Fz dans l'époque
            vu_c = net.clignes(Xte7)
            vrai_s = (VS[te] > 25.0).sum(axis=1) == 1  # un saut net sur une seule électrode
            dd = det[nom]
            dd["cl_vrai"] += vrai_c.sum(); dd["cl_vu"] += vu_c.sum(); dd["cl_juste"] += (vu_c & vrai_c).sum()
            dd["sa_vrai"] += vrai_s.sum(); dd["sa_vu"] += isole.sum(); dd["sa_juste"] += (isole & vrai_s).sum()
            print(f"  [{nom}] sujet {s} : {int(mcal.sum()):3d} clignements en calibration, "
                  f"{'projection' if proj else 'PAS DE PROJECTION'} ; AUC aucun {res[(nom, 'aucun')][-1]:.3f} "
                  f"rejet {res[(nom, 'rejet')][-1]:.3f} ciblé {res[(nom, 'ciblé')][-1]:.3f}")
    print("\n  " + f"{'scénario':38s} " + " | ".join(f"{m:>12s}" for m in meths) + " | gardé (rejet)")
    for nom in SCEN:
        print(f"  {nom:38s} " + " | ".join(f"{np.mean(res[(nom, m)]):.3f} ±{np.std(res[(nom, m)]):.2f}"
                                          for m in meths) + f" | {np.mean(garde[nom]):.0%}")
    print("\n  Par sujet, écart à « propre » (même sujet) : aucun / rejet / ciblé ; projection appliquée sur k/6")
    for nom in SCEN[1:]:
        dl = lambda m: np.array(res[(nom, m)]) - np.array(res[("propre", "aucun")])
        print(f"  {nom:38s} " + " | ".join(m + " " + " ".join(f"{v:+.3f}" for v in dl(m))
                                          for m in ("aucun", "rejet", "ciblé"))
              + f" | projection {sum(proj_par_sujet[nom])}/{len(subjects)}")
    print("\n  Détection sur le test, contre la vérité injectée :")
    for nom in SCEN:
        dd = det[nom]
        f = lambda j, v: f"{j / v:.0%}" if v else "  - "
        print(f"  {nom:38s} clignements : rappel {f(dd['cl_juste'], dd['cl_vrai'])} précision "
              f"{f(dd['cl_juste'], dd['cl_vu'])} ({dd['cl_vrai']} vrais, {dd['cl_vu']} vus) | sauts : rappel "
              f"{f(dd['sa_juste'], dd['sa_vrai'])} précision {f(dd['sa_juste'], dd['sa_vu'])} ({dd['sa_vrai']} vrais, {dd['sa_vu']} vus)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
