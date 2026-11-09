"""Convertit BNCI 2015-013 (fichiers .mat d'errp-bench) en jeu UBRI-NRD LOCAL.

Lancer : .venv/bin/python -B convertir_bnci.py <dossier de sortie neuf>
Source : $UBRI_ERRP_DATA, sinon ../errp-bench/data. Licence CC-BY-NC-ND : le jeu converti ne se publie pas, d'où
consent_publication = False pour tous (l'export public doit tout refuser).

Un épisode = un bloc (run). Le jeu n'a pas d'horloge commune : les blocs sont posés bout à bout avec un écart FICTIF
de 60 s, et info.json le déclare (clock_sync).
"""
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io as sio

from ubri_nrd.io import ecrire_eeg, ecrire_meta
from ubri_nrd.schema import EPISODE_COLUMNS, EVENT_COLUMNS, PARTICIPANT_COLUMNS

DATA = Path(os.environ.get("UBRI_ERRP_DATA", Path(__file__).resolve().parent.parent / "errp-bench" / "data"))
FS = 512
GAP_S = 60.0
CORRECT, ERROR = {5, 10}, {6, 9}
EXPECTED = [(s, k) for s in range(1, 7) for k in (1, 2)]


def evenement(code: int) -> tuple[str, dict]:
    if code in ERROR:
        return "robot_error_injected", {"error_kind": "mauvaise_direction", "planned": True}
    if code in CORRECT:
        return "robot_correct", {}
    return "source_event", {}


def convertir(dest: Path) -> None:
    if dest.exists():
        sys.exit(f"{dest} existe déjà : la conversion écrit dans un dossier neuf")
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    channels = None
    episodes, events = [], []
    t_next, ei = 0.0, 0
    for s, k in EXPECTED:
        f = DATA / f"Subject0{s}_s{k}.mat"
        runs = np.atleast_1d(sio.loadmat(f, squeeze_me=True, struct_as_record=False)["run"])
        for ri, r in enumerate(runs, start=1):
            h = r.header
            assert int(h.Subject) == s and int(h.Session) == k and int(h.SampleRate) == FS, f"{f.name} run {ri}"
            labels = [str(x) for x in np.atleast_1d(h.Label)][:64]
            channels = channels or labels
            assert labels == channels, f"{f.name} run {ri} : ordre des canaux différent"
            eeg = np.asarray(r.eeg)
            assert eeg.ndim == 2 and eeg.shape[1] == 64, eeg.shape
            pos = np.atleast_1d(h.EVENT.POS).astype(np.int64)
            typ = np.atleast_1d(h.EVENT.TYP).astype(np.int64)
            assert np.all(np.diff(pos) > 0) and pos.min() >= 1 and pos.max() <= len(eeg), f"{f.name} run {ri} : POS"
            n = len(eeg)
            t = t_next + np.arange(n) / FS
            # enregistrement complet du jeu d'origine : idx = position de l'échantillon dans le bloc ; pertes NON
            # annoncées (None) : le .mat ne dit rien des pertes éventuelles de l'acquisition d'origine
            rel = ecrire_eeg(tmp, ei, t, eeg, channels, idx=np.arange(n, dtype=np.int64))
            episodes.append((ei, f"sub-{s:02d}", k, ri, float(t[0]), float(t[-1]), n, rel, 0, n - 1, None))
            for p, c in zip(pos, typ):
                typ_nrd, det = evenement(int(c))
                events.append((float(t_next + (p - 1) / FS), ei, typ_nrd, json.dumps(det), int(c)))
            t_next = float(t[-1]) + GAP_S
            ei += 1
    participants = pd.DataFrame(
        [(f"sub-{s:02d}", None, None, True, False, False, "BNCI 2015-013") for s in range(1, 7)],
        columns=PARTICIPANT_COLUMNS)
    info = {
        "agent": "curseur_ecran",
        "eeg_device": "Biosemi ActiveTwo, 64 canaux (jeu d'origine)",
        "fs": FS,
        "unit": "µV",
        "clock_sync": {"method": "aucune : jeu converti, blocs posés bout à bout avec 60 s fictives", "drift_ppm": None},
        "source": "BNCI 2015-013, Chavarriaga & Millán 2010 (EPFL)",
        "licence": "CC-BY-NC-ND-4.0",
    }
    montage = {"channels": channels, "systeme": "10-20 étendu (Biosemi 64)", "reference": "brute (CMS/DRL Biosemi)",
               "electrode_type": "gel"}
    ecrire_meta(tmp, info, montage, participants, pd.DataFrame(episodes, columns=EPISODE_COLUMNS),
                pd.DataFrame(events, columns=EVENT_COLUMNS))
    tmp.rename(dest)
    print(f"OK : {ei} épisodes, {len(events)} événements -> {dest}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    convertir(Path(sys.argv[1]))
