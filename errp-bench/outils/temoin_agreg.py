"""Dispersion du témoin agrégé (device7 réaliste, générique, sans bruit) sur 6 graines.
Lancer depuis errp-bench : .venv/bin/python -B outils/temoin_agreg.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106 : chemin relatif au fichier
import numpy as np
from sklearn.linear_model import LogisticRegression
from pyriemann.estimation import XdawnCovariances
from pyriemann.tangentspace import TangentSpace
from prep import OUT
from bench import DEVICE, device_idx, reref, K_MAX, agreg_meme, agreg_personnes
d=np.load(OUT); X=d["X"].astype(np.float64); y=d["y"].astype(int); subj=d["subject"]
ch=DEVICE["device7"]; XB=reref(X[:, device_idx(ch)], ch)
feats=[]
for s in range(1,7):
    own=np.where(subj==s)[0]; oth=np.where(subj!=s)[0]; te=own[K_MAX:]
    xc=XdawnCovariances(nfilter=4, estimator="lwf").fit(XB[oth], y[oth])
    Co=xc.transform(XB[oth]); ts=TangentSpace(metric="riemann").fit(Co)
    feats.append((ts.transform(Co), ts.transform(xc.transform(XB[te])), y[oth], y[te]))
for seed in range(6):
    ps=[]
    for Fo,Ft,yo,yt in feats:
        yy=yo.copy(); np.random.default_rng(seed).shuffle(yy)
        lr=LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(Fo,yy)
        ps.append((lr.predict_proba(Ft)[:,1], yt))
    rng=np.random.default_rng(100+seed)
    m=[np.mean([agreg_meme(p,t,n,rng) for p,t in ps]) for n in (1,5,10)]
    q=[agreg_personnes(ps,n,rng) for n in (1,5)]
    print(f"graine {seed} : même personne n=1 {m[0]:.3f} n=5 {m[1]:.3f} n=10 {m[2]:.3f} | personnes n=1 {q[0]:.3f} n=5 {q[1]:.3f}")
