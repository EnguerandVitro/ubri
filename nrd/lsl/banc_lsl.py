"""Banc de la chaîne d'enregistrement LSL, avec flux SIMULÉS : EEG et événements (l'état du robot est émis et compté,
pas encore vérifié en temps ni écrit au format).

Lancer depuis products/ubri/nrd :
  PYLSL_LIB=$PWD/vendor/liblsl/lib/liblsl.so .venv/bin/python -B lsl/banc_lsl.py sorties/lsl-banc [--duree 60]

Deux processus : le simulateur (casque + robot) et l'enregistreur, qui se trouvent par LSL comme sur un réseau.
Le casque simulé a un quartz qui DÉRIVE (DRIFT_PPM) et envoie des paquets avec une latence de type Bluetooth
(LAT_BASE + gigue uniforme). Les marqueurs d'événements sont horodatés par le contrôleur du robot avec SA latence
logicielle (--mk-lat + gigue uniforme --mk-jit). La VÉRITÉ (instant réel de chaque échantillon et événement) est écrite
par le simulateur dans verite.json. L'enregistreur n'y lit, pendant la capture, que le NOMBRE d'échantillons envoyés
(condition d'arrêt) ; le reste n'est lu qu'à l'évaluation, et aucun traitement ne l'utilise.

Traitement de l'enregistreur : dejitter linéaire (régression horodatage ~ indice), puis latence calibrée sur les
impulsions EEG des N_CAL premiers événements (une photodiode le ferait en vrai), validée sur les suivants.
La calibration mesure l'écart EEG <-> marqueur : l'heure ABSOLUE de l'EEG n'est pas récupérable (affichée pour
information), seul l'alignement RELATIF l'est, et c'est lui qui compte pour un ErrP.
Critères (chacun affiché OK/KO) : alignement événement/EEG < 1 ms sur chaque événement de test (contre la vérité, en
ms ; le rattachement au « premier échantillon après » est discret, ce n'est pas un critère) ; chaque événement à au
plus 1 échantillon de son impulsion ; au moins 5 événements de test ; jeu UBRI-NRD valide.
Cassures : --sans-calibration (latence non corrigée) ; --mk-jit 0.003 (gigue des marqueurs > 1 ms : l'alignement
seul doit rougir) ; --casse-resolution (l'enregistreur cherche un flux inexistant : sortie non nulle bornée, KE#161).
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106

FS = 250.0  # cadence NOMINALE du casque
DRIFT_PPM = 50.0  # le quartz du casque va 50 ppm trop vite
CHUNK = 10  # échantillons par paquet
LAT_BASE, LAT_JIT = 0.015, 0.010  # latence de transport : 15 ms + uniforme [0, 10 ms]
N_CH = 7  # device UBRI : Fz FCz Cz CPz Pz C3 C4
CHANNELS = ["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4"]
PULSE_UV = 500.0  # impulsion de synchronisation sur un canal dédié (photodiode simulée)
FS_ROBOT = 50.0
EVENT_PERIOD = 2.0
P_ERROR = 0.25
N_CAL = 10  # événements utilisés pour calibrer la latence (les suivants servent de test)


def simulateur(tag: str, duree: float, verite_path: str, pret, drift_ppm: float, mk_lat: float, mk_jit: float) -> None:
    import pylsl
    rng = np.random.default_rng(1)
    fs_vrai = FS * (1 + drift_ppm * 1e-6)
    eeg_info = pylsl.StreamInfo(f"ubri-eeg-{tag}", "EEG", N_CH + 1, FS, "float32", f"eeg-{tag}")
    rob_info = pylsl.StreamInfo(f"ubri-robot-{tag}", "RobotState", 2, FS_ROBOT, "float32", f"robot-{tag}")
    mk_info = pylsl.StreamInfo(f"ubri-events-{tag}", "Markers", 1, 0, "string", f"events-{tag}")
    eeg_out, rob_out, mk_out = (pylsl.StreamOutlet(i, CHUNK) for i in (eeg_info, rob_info, mk_info))
    if not pret.wait(timeout=60):  # borne dure côté simulateur aussi (KE#161)
        raise SystemExit("simulateur : enregistreur jamais connecté en 60 s")
    t0 = pylsl.local_clock() + 1.0
    n_eeg = int(duree * fs_vrai)
    t_eeg = t0 + np.arange(n_eeg) / fs_vrai  # VÉRITÉ : instant réel de chaque échantillon
    n_ev = int((duree - 2) / EVENT_PERIOD)
    t_ev = t0 + 1.0 + EVENT_PERIOD * np.arange(n_ev) + rng.uniform(0, 0.5, n_ev)
    kinds = np.where(rng.random(n_ev) < P_ERROR, "robot_error_injected", "robot_correct")
    pulse_idx = np.searchsorted(t_eeg, t_ev)  # l'impulsion tombe sur le premier échantillon après l'événement
    eeg = (rng.standard_normal((n_eeg, N_CH + 1)) * 10).astype(np.float32)
    eeg[:, N_CH] = 0
    eeg[pulse_idx, N_CH] = PULSE_UV
    n_rob = int(duree * FS_ROBOT)
    t_rob = t0 + np.arange(n_rob) / FS_ROBOT
    i_eeg = i_rob = i_ev = 0
    lat = []
    while i_eeg + CHUNK <= n_eeg:
        now = pylsl.local_clock()
        if i_ev < n_ev and now >= t_ev[i_ev] + mk_lat + mk_jit:
            # marqueur logiciel : horodaté par le contrôleur du robot, avec sa propre latence (constante + gigue)
            mk_out.push_sample([json.dumps({"type": str(kinds[i_ev]), "action_id": int(i_ev)})],
                               float(t_ev[i_ev] + mk_lat + rng.uniform(0, mk_jit)))
            i_ev += 1
        while i_rob < n_rob and now >= t_rob[i_rob]:
            rob_out.push_sample([float(i_rob % 100), float(i_ev)], float(t_rob[i_rob]))
            i_rob += 1
        last = i_eeg + CHUNK - 1
        if now >= t_eeg[last] + LAT_BASE + rng.uniform(0, LAT_JIT):
            # L'hôte ne connaît pas l'instant réel : il horodate le paquet À SON ARRIVÉE (comme un vrai pilote).
            eeg_out.push_chunk(eeg[i_eeg:last + 1].tolist(), pylsl.local_clock())
            lat.append(pylsl.local_clock() - t_eeg[last])
            i_eeg += CHUNK
        time.sleep(0.0005)
    verite = {"t_eeg0": float(t_eeg[0]), "fs_vrai": fs_vrai, "n_eeg_envoyes": i_eeg, "t_ev": t_ev[:i_ev].tolist(),
              "kinds": kinds[:i_ev].tolist(), "pulse_idx": pulse_idx[:i_ev].tolist(),
              "latence_moy_s": float(np.mean(lat))}
    tmp = verite_path + ".tmp"
    Path(tmp).write_text(json.dumps(verite))
    os.replace(tmp, verite_path)
    time.sleep(1.5)  # laisse l'enregistreur vider ses tampons


def resoudre(pylsl, source_id: str):
    r = pylsl.resolve_byprop("source_id", source_id, 1, 10.0)
    if not r:
        raise SystemExit(f"flux {source_id} introuvable en 10 s")
    return pylsl.StreamInlet(r[0], max_buflen=360, processing_flags=pylsl.proc_clocksync)


def enregistrer(tag: str, duree: float, verite_path: Path, pret):
    import pylsl
    eeg_in, rob_in, mk_in = (resoudre(pylsl, f"{k}-{tag}") for k in ("eeg", "robot", "events"))
    for i in (eeg_in, rob_in, mk_in):
        i.open_stream(timeout=10.0)
    pret.set()
    xs, ts, rts, mks, mts = [], [], [], [], []
    fin = time.monotonic() + duree + 20  # borne dure (KE#161)
    while time.monotonic() < fin:
        x, t = eeg_in.pull_chunk(timeout=0.05)
        if t:
            xs += x; ts += t
        _, t = rob_in.pull_chunk(timeout=0.0)
        rts += t
        m, t = mk_in.pull_chunk(timeout=0.0)
        if t:
            mks += [s[0] for s in m]; mts += t
        if verite_path.exists():
            v = json.loads(verite_path.read_text())
            if len(ts) >= v["n_eeg_envoyes"]:  # progression positive tirée de la SOURCE (KE#131)
                break
    else:
        raise SystemExit(f"enregistrement incomplet : {len(ts)} échantillons reçus avant la borne")
    return np.asarray(xs, np.float64), np.asarray(ts), np.asarray(rts), mks, np.asarray(mts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sortie")
    ap.add_argument("--duree", type=float, default=60.0)
    ap.add_argument("--drift", type=float, default=DRIFT_PPM, help="dérive du quartz simulé (ppm)")
    ap.add_argument("--sans-calibration", action="store_true", help="CASSURE : latence non corrigée")
    ap.add_argument("--mk-lat", type=float, default=0.002, help="latence logicielle des marqueurs (s)")
    ap.add_argument("--mk-jit", type=float, default=0.0003, help="gigue uniforme des marqueurs (s)")
    ap.add_argument("--casse-resolution", action="store_true", help="CASSURE : l'enregistreur cherche un flux absent")
    a = ap.parse_args()
    out = Path(a.sortie)
    if out.exists():
        raise SystemExit(f"{out} existe déjà")
    work = out.with_name(out.name + ".travail")
    if work.exists():
        raise SystemExit(f"{work} existe : exécution précédente interrompue, à examiner puis retirer à la main")
    work.mkdir(parents=True)
    verite_path = work / "verite.json"
    tag = uuid.uuid4().hex[:12]
    pret = mp.Event()
    sim = mp.Process(target=simulateur, args=(tag, a.duree, str(verite_path), pret, a.drift, a.mk_lat, a.mk_jit))
    sim.start()
    try:
        x, t_raw, t_rob, mks, t_mk = enregistrer("absent-" + tag if a.casse_resolution else tag, a.duree,
                                                 verite_path, pret)
    finally:
        sim.join(timeout=30)
        if sim.is_alive():  # jamais d'attente sans borne d'un enfant (KE#161)
            sim.terminate()
            sim.join(timeout=5)
    if sim.exitcode != 0:
        raise SystemExit(f"simulateur : code {sim.exitcode}")

    # --- Traitement de l'enregistreur : il ne voit QUE ce qu'il a reçu. ---
    n = len(t_raw)
    idx = np.arange(n)
    b, a0 = np.polyfit(idx, t_raw, 1)  # dejitter : t = a0 + b*i
    t_dej = a0 + b * idx
    ev = [json.loads(s) for s in mks]
    pulses = np.where(x[:, N_CH] > PULSE_UV / 2)[0]
    if len(pulses) != len(ev):
        raise SystemExit(f"{len(pulses)} impulsions pour {len(ev)} événements")
    # latence : écart moyen (impulsion - événement) sur les N_CAL premiers, moins le demi-pas attendu
    # (l'impulsion tombe sur le premier échantillon APRÈS l'événement, en moyenne un demi-échantillon plus tard).
    ecarts = t_dej[pulses[:N_CAL]] - t_mk[:N_CAL]
    latence = 0.0 if a.sans_calibration else float(np.mean(ecarts) - b / 2)
    t_cal = t_dej - latence
    ev_sample = np.searchsorted(t_cal, t_mk)  # rattachement événement -> échantillon, avec l'horloge calibrée

    # --- Évaluation contre la VÉRITÉ (lue en entier seulement maintenant ; avant : le seul nombre envoyé). ---
    v = json.loads(verite_path.read_text())
    assert n == v["n_eeg_envoyes"], (n, v["n_eeg_envoyes"])  # aucun échantillon perdu
    vrai = v["t_eeg0"] + idx / v["fs_vrai"]
    err_raw = t_raw - vrai
    err_dej = t_dej - vrai
    err_cal = t_cal - vrai
    drift_est = (1 / b / FS - 1) * 1e6
    test = slice(N_CAL, None)
    p_vrai = np.asarray(v["pulse_idx"])
    # alignement : (instant de l'impulsion - instant de l'événement) vu par l'enregistreur, moins le même écart réel
    align_ms = ((t_cal[p_vrai] - t_mk) - (vrai[p_vrai] - np.asarray(v["t_ev"])))[test] * 1e3
    a_un_echantillon = bool(np.all(np.abs(ev_sample[test] - p_vrai[test]) <= 1))
    ev_err_ms = (t_mk - np.asarray(v["t_ev"])) * 1e3
    res = {
        "échantillons reçus / envoyés": f"{n} / {v['n_eeg_envoyes']}",
        "événements": len(ev), "dont erreurs injectées": sum(e["type"] == "robot_error_injected" for e in ev),
        "état robot (échantillons)": len(t_rob),
        "erreur brute (ms) : moyenne / max": (err_raw.mean() * 1e3, np.abs(err_raw).max() * 1e3),
        "après dejitter (ms) : moyenne / max": (err_dej.mean() * 1e3, np.abs(err_dej).max() * 1e3),
        "heure absolue après calibration (ms, max, information : décalée de la latence des marqueurs)":
            np.abs(err_cal).max() * 1e3,
        "latence estimée / vraie moyenne (ms)": (latence * 1e3, v["latence_moy_s"] * 1e3),
        "dérive estimée / vraie (ppm)": (drift_est, a.drift),
        "erreur d'horodatage des événements (ms, max)": np.abs(ev_err_ms).max(),
        f"alignement événement/EEG, {len(ev) - N_CAL} événements de test (ms, max)": float(np.abs(align_ms).max()),
        "chaque événement à au plus 1 échantillon de son impulsion": a_un_echantillon,
    }
    for k, val in res.items():
        print(f"{k} : " + (" / ".join(f"{z:.3f}" for z in val) if isinstance(val, tuple) else
                            f"{val:.3f}" if isinstance(val, float) else str(val)))
    criteres = {
        "alignement < 1 ms": bool(np.abs(align_ms).max() < 1.0),
        "à au plus 1 échantillon": a_un_echantillon,
        "au moins 5 événements de test": len(ev) - N_CAL >= 5,
    }

    # --- Écriture UBRI-NRD (horloge calibrée) puis validation. ---
    import pandas as pd
    from ubri_nrd.io import ecrire_eeg, ecrire_meta
    from ubri_nrd.schema import EPISODE_COLUMNS, EVENT_COLUMNS, PARTICIPANT_COLUMNS
    from ubri_nrd.valider import valider
    nrd = work / "nrd"
    # flux simulé continu, sans compteur d'appareil : idx = rang de l'échantillon reçu ; pertes non annoncées
    rel = ecrire_eeg(nrd, 0, t_cal, x[:, :N_CH], CHANNELS, idx=np.arange(n, dtype=np.int64))
    evs = []
    for e, te in zip(ev, t_mk):
        det = {"action_id": e["action_id"]}
        if e["type"] == "robot_error_injected":
            det.update(error_kind="mauvais_objet", planned=True)
        evs.append((float(te), 0, e["type"], json.dumps(det), None))
    info = {"agent": "robot", "eeg_device": "SIMULÉ (banc LSL)", "fs": FS, "unit": "µV",
            "clock_sync": {"method": "LSL clocksync + dejitter linéaire + latence calibrée sur impulsions",
                           "drift_ppm": round(drift_est, 2), "latence_ms": round(latence * 1e3, 3),
                           "residu_max_ms": round(float(np.abs(err_cal).max() * 1e3), 4)},
            "source": "banc_lsl.py (données simulées)", "licence": "CC-BY-4.0"}
    ecrire_meta(nrd, info, {"channels": CHANNELS, "electrode_type": "simulée"},
                pd.DataFrame([("sim-01", None, None, True, False, False, "simulation")], columns=PARTICIPANT_COLUMNS),
                pd.DataFrame([(0, "sim-01", 1, 1, float(t_cal[0]), float(t_cal[-1]), n, rel, 0, n - 1, None)],
                             columns=EPISODE_COLUMNS),
                pd.DataFrame(evs, columns=EVENT_COLUMNS))
    err, cnt = valider(nrd)
    print("jeu UBRI-NRD :", "VALIDE" if not err else f"INVALIDE {err[:3]}", cnt)
    criteres["jeu UBRI-NRD valide"] = not err
    for k, v in criteres.items():
        print(f"  {'OK' if v else 'KO'}  {k}")
    ok = all(criteres.values())
    work.rename(out)
    print("CRITÈRES TENUS" if ok else "CRITÈRES NON TENUS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
