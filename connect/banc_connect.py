"""Banc de bout en bout d'UBRI Connect : carte simulée (trames UBRI v0 sur un tube) -> diffuseur LSL -> enregistreur.

Lancer depuis products/ubri/connect, avec l'environnement de ../nrd :
  PYLSL_LIB=../nrd/vendor/liblsl/lib/liblsl.so ../nrd/.venv/bin/python -B banc_connect.py <sortie neuve> [options]
Options : --duree 120 --drift 50 --mk-lat 0.002 --mk-jit 0.0003 --perte 0.0 (fraction de paquets jamais envoyés)
          --sans-calibration (CASSURE) --dejitter-indice (CASSURE : régression sur la position reçue, pas le compteur)
          --casse-diffuseur (CASSURE : le diffuseur jette une trame décodée -> « comptées = annoncées » doit rougir)
          --trame 3 (défaut : ouverture + v3 + clôture ; 1 et 2 = anciens formats, tête/queue lues dans la vérité)
          --rafales-drdy N (N rafales de 3 DRDY ratées par l'interruption)
          --compteur-isr (CASSURE : compteur tenu par l'interruption, les DRDY ratées disparaissent sans trace)
          --ignorer-ouverture (CASSURE : l'enregistreur suppose un départ à 0 ; la carte part de 1000)

Critères (chacun OK/KO) : alignement événement/EEG < 1 ms sur les événements de test ; aucun événement mal apparié ;
ancres perdues signalées = réelles ; pertes COMPTÉES par l'enregistreur (compteurs reçus + ouverture + clôture) =
pertes ANNONCÉES (v3 : clôture de la carte ; v1/v2 : compte interne du simulateur) ; en v3 en plus, ÉTENDUE annoncée
(ouverture -> clôture) = DRDY réelles (vérité) et session cohérente ; au moins 5 événements de test ; si aucune
perte : jeu UBRI-NRD écrit et VALIDE. Les comptées télescopent (dernier - départ + 1 - reçus) : chaque critère est
nommé par ce à quoi il se réduit. Cassures : le diffuseur qui jette une trame trompe « annoncées » (v1, v2, v3) ;
le compteur d'interruption trompe « étendue » (la carte et l'hôte sont alors aveugles ensemble).
          --rafale-finale (les 3 dernières DRDY ratées : la clôture doit porter la dernière valeur du TIMER)
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

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))  # KE#106
sys.path.insert(0, str(ICI.parent / "nrd"))
from ubri_connect import chaine  # noqa: E402

FS, PAQUET, LAT_BASE, LAT_JIT, N_CAL = 250.0, 10, 0.015, 0.010, 10


def _enfant(fermer, cible, *args):
    """Chaque enfant ferme les extrémités du tube qu'il n'utilise pas : sinon le lecteur ne voit jamais la fin."""
    for fd in fermer:
        os.close(fd)
    cible(*args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sortie"); ap.add_argument("--duree", type=float, default=120.0)
    ap.add_argument("--drift", type=float, default=50.0); ap.add_argument("--mk-lat", type=float, default=0.002)
    ap.add_argument("--mk-jit", type=float, default=0.0003); ap.add_argument("--perte", type=float, default=0.0)
    ap.add_argument("--trame", type=int, choices=(1, 2, 3), default=3)
    ap.add_argument("--rafales-drdy", type=int, default=0)
    ap.add_argument("--compteur-isr", action="store_true"); ap.add_argument("--ignorer-ouverture", action="store_true")
    ap.add_argument("--rafale-finale", action="store_true")
    ap.add_argument("--casse-diffuseur", action="store_true")
    ap.add_argument("--sans-calibration", action="store_true"); ap.add_argument("--dejitter-indice", action="store_true")
    a = ap.parse_args()
    if a.ignorer_ouverture and a.trame != 3:
        ap.error("--ignorer-ouverture n'a de sens qu'avec --trame 3 (seule la v3 a une ouverture)")
    out = Path(a.sortie)
    work = out.with_name(out.name + ".travail")
    if out.exists() or work.exists():
        raise SystemExit(f"{out} ou {work} existe déjà : à examiner puis retirer à la main")
    work.mkdir(parents=True)
    verite = str(work / "verite.json")
    tag = uuid.uuid4().hex[:12]
    rng = np.random.default_rng(0)
    t0 = time.monotonic() + 3.0  # = pylsl.local_clock (CLOCK_MONOTONIC, écart mesuré < 10 µs)
    t_ev, kinds = chaine.plan_evenements(a.duree, t0, 2.0, 0.25, rng)
    fin = time.monotonic() + a.duree + 40  # borne dure de tous les processus (KE#161)
    r_fd, w_fd = os.pipe()
    depart = 1000 if a.trame == 3 else 0
    procs = [mp.Process(target=_enfant, name="carte", args=([r_fd], chaine.carte_simulee, w_fd, t0, a.duree, FS, a.drift,
                                                            PAQUET, LAT_BASE, LAT_JIT, t_ev.tolist(), verite, 1, a.trame,
                                                            a.perte, a.rafales_drdy,
                                                            "isr" if a.compteur_isr else "timer", depart,
                                                            a.rafale_finale)),
             mp.Process(target=_enfant, name="diffuseur", args=([w_fd], chaine.diffuseur, r_fd, tag, FS, fin,
                                                                5000 if a.casse_diffuseur else None)),
             mp.Process(target=_enfant, name="robot", args=([r_fd, w_fd], chaine.robot_simule, tag, t_ev.tolist(),
                                                           kinds, a.mk_lat, a.mk_jit, fin))]
    for p in procs:
        p.start()
    os.close(r_fd); os.close(w_fd)  # le parent ne garde aucune extrémité du tube
    import pylsl
    ecart = abs(pylsl.local_clock() - time.monotonic())
    if ecart > 1e-3:  # t0 a été fixé avec time.monotonic avant l'import : les deux horloges doivent être la même
        raise SystemExit(f"local_clock et monotonic diffèrent de {ecart * 1e3:.3f} ms : t0 invalide")
    try:
        def inlet(sid):
            r = pylsl.resolve_byprop("source_id", f"{sid}-{tag}", 1, 15.0)
            if not r:
                raise SystemExit(f"flux {sid}-{tag} introuvable en 15 s")
            i = pylsl.StreamInlet(r[0], max_buflen=360, processing_flags=pylsl.proc_clocksync)
            i.open_stream(timeout=10.0)
            return i
        sess_in = inlet("session")  # créé par le diffuseur avant toute donnée
        eeg_in, mk_in = inlet("eeg"), inlet("events")
        xs, ts, mks, mts, session = [], [], [], [], None
        while time.monotonic() < fin:
            x, t = eeg_in.pull_chunk(timeout=0.05)
            if t:
                xs += x; ts += t
            m, t = mk_in.pull_chunk(timeout=0.0)
            if t:
                mks += [s[0] for s in m]; mts += t
            s, _ = sess_in.pull_sample(timeout=0.0)
            if s:
                session = json.loads(s[0])
            if (Path(verite).exists() and procs[1].exitcode is not None and len(mks) == len(t_ev)
                    and session is not None):
                x, t = eeg_in.pull_chunk(timeout=0.5)  # vider le reste
                if t:
                    xs += x; ts += t
                if not t:
                    break
        else:
            raise SystemExit(f"borne atteinte : {len(ts)} échantillons, {len(mks)} événements, "
                             f"message de session {'reçu' if session else 'ABSENT'}")
    finally:
        for p in procs:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate(); p.join(timeout=5)
    for p in procs:
        if p.exitcode != 0:
            raise SystemExit(f"processus {p.name} : code {p.exitcode}")

    x = np.asarray(xs, float); t_raw = np.asarray(ts); t_mk = np.asarray(mts)
    compteur = x[:, -1].astype(np.int64); sync = x[:, -2]; front = x[:, -3]
    if a.dejitter_indice:  # CASSURE : la position reçue remplace le compteur (les pertes décalent tout)
        compteur_vu = np.arange(len(compteur)); front = None
    else:
        compteur_vu = compteur
    if not np.all(np.diff(compteur) > 0):
        print("  KO  compteur reçu non strictement croissant (doublon ou désordre)")
        work.rename(out)
        print("CRITÈRES NON TENUS")
        return 1
    try:
        r = chaine.traiter(t_raw, compteur_vu, sync, t_mk, N_CAL, FS, front if a.trame >= 2 else None)
    except ValueError as e:
        print(f"  KO  calibration impossible : {e}")
        work.rename(out)
        print("CRITÈRES NON TENUS")
        return 1
    lat = 0.0 if a.sans_calibration else r["latence"]
    t_cal = r["a0"] + r["pas"] * compteur_vu - lat
    t_de = lambda c: r["a0"] + r["pas"] * c - lat  # heure calibrée de n'importe quel compteur

    v = json.loads(Path(verite).read_text())
    # Vérité : en mode « isr » le compteur porté n'est plus l'indice réel ; on passe toujours par la table.
    c2i = dict(zip(v["compteurs_servis"], v["indices_servis"])); i2c = {i: c for c, i in c2i.items()}
    sidx = np.asarray(v["sync_idx"])
    paire = r["paire"]
    vus = set(compteur[sync > 0.5].tolist()) | (set(int(f) for f in x[:, -3] if f >= 0) if a.trame >= 2 else set())
    ancre_vraie = np.array([i2c[int(i)] in vus for i in sidx[:len(t_mk)]])
    test = [i for i in range(len(t_mk)) if i not in set(r["cal"].tolist()) and paire[i] >= 0]
    mal_apparies = sum(1 for i in range(len(t_mk)) if paire[i] >= 0 and paire[i] != i2c[int(sidx[i])])
    vrai_de = lambda c: v["t_ech0"] + c2i[int(c)] / v["fs_vrai"]
    align = [((t_de(paire[i]) - t_mk[i]) - (vrai_de(paire[i]) - t_ev[i])) * 1e3 for i in test]
    # Pertes, deux jambes INDÉPENDANTES :
    # - RÉELLES (vérité) : échantillons jamais reçus, quelle qu'en soit la cause ;
    # - ANNONCÉES par la carte dans la clôture (DRDY manquées + trames abandonnées), lues dans le flux.
    # Les COMPTÉES viennent des seuls compteurs reçus + ouverture (départ) + clôture (dernier émis).
    reelles = v["n"] - len(compteur)
    o, c = session.get("ouverture"), session.get("cloture")
    if a.trame == 3:
        if o is None or c is None:
            raise SystemExit("trame 3 sans ouverture ou sans clôture décodée : session inexploitable")
        depart, dernier, annoncees = (0 if a.ignorer_ouverture else o["compteur_depart"]), c["dernier_emis"], \
            c["drdy_manquees"] + c["abandonnees"]
    else:  # anciens formats : tête/queue ET annonce lues dans la vérité (c'est la limite que la v3 ferme) ;
        # l'annonce = ce que la carte aurait su compter (compte interne du simulateur)
        depart, dernier = v["depart"], v["depart"] + v["n"] - 1
        annoncees = (v["drdy_ratees"] if v["compteur"] == "timer" else 0) + v["trames_abandonnees"]
    comptees = (int(compteur[0]) - depart) + int((np.diff(compteur) - 1).sum()) + (dernier - int(compteur[-1]))
    print(f"échantillons reçus {len(compteur)} / DRDY {v['n']} ; pertes réelles {reelles} (DRDY ratées "
          f"{v['drdy_ratees']}, trames abandonnées {v['trames_abandonnees']}), comptées {comptees}, annoncées "
          f"{annoncees} ; compteur {v['compteur']}, départ annoncé {o['compteur_depart'] if o else '-'} ; "
          f"pertes vues par le décodeur du diffuseur {session.get('perdus_decodeur')}")
    print(f"dérive estimée {r['derive_ppm']:.2f} ppm (vraie {a.drift}) ; latence estimée {r['latence'] * 1e3:.3f} ms")
    print(f"alignement événement/EEG, {len(align)} événements de test : max {np.abs(align).max():.3f} ms")
    print(f"trame v{a.trame} ; ancres perdues : {r['ancres_perdues']} signalées, {int((~ancre_vraie).sum())} réelles ; "
          f"événements mal appariés : {mal_apparies}")
    crit = {"alignement < 1 ms": bool(np.abs(align).max() < 1.0),
            "aucun événement mal apparié": mal_apparies == 0,
            "ancres perdues signalées = réelles": r["ancres_perdues"] == int((~ancre_vraie).sum()),
            # Les comptées TÉLESCOPENT : (c0 - départ) + Σ(diff - 1) + (dernier - c_last) = dernier - départ + 1 - reçus.
            # Seule une comparaison à une ANNONCE de la source voit une trame perdue côté hôte (KE#92, revue P1-1).
            "pertes comptées = pertes annoncées" + (" par la carte (clôture)" if a.trame == 3 else
                                                     " (compte interne du simulateur)"): comptees == annoncees,
            "au moins 5 événements de test": len(align) >= 5}
    if a.trame == 3:
        # se réduit à « étendue ouverture -> clôture = DRDY réelles » : ne regarde pas les trames reçues, mais voit
        # un compteur qui ne compte pas toutes les DRDY (cassure --compteur-isr) ou un départ mal lu
        crit["étendue annoncée (ouverture -> clôture) = DRDY réelles (vérité)"] = comptees == reelles
        crit["session cohérente (ouverture conforme, aucune incohérence)"] = (
            not session["incoherences"] and o["voies"] == chaine.CANAUX and o["fs"] == int(FS))
        if session["incoherences"]:
            print(f"incohérences : {session.get('incoherences_total')} au total ; premières :", session["incoherences"][:3])

    # Jeu UBRI-NRD v0.2, écrit AVEC ou SANS pertes : idx = compteur de la carte. En v3, l'étendue vient de
    # l'ouverture (départ) et de la clôture (dernier émis) et les pertes annoncées de la clôture ; en v1/v2 (pas de
    # trames de session) l'étendue se limite aux compteurs reçus et les pertes restent NON annoncées : on n'emprunte
    # jamais la vérité du simulateur pour remplir le format.
    import pandas as pd
    from ubri_nrd.io import ecrire_eeg, ecrire_meta
    from ubri_nrd.schema import EPISODE_COLUMNS, EVENT_COLUMNS, PARTICIPANT_COLUMNS
    from ubri_nrd.valider import valider
    if a.trame == 3:
        i0, i1, pertes = depart, dernier, annoncees
    else:
        i0, i1, pertes = int(compteur[0]), int(compteur[-1]), None
    nrd = work / "nrd"
    rel = ecrire_eeg(nrd, 0, t_cal, x[:, :len(chaine.CANAUX)] * chaine.UV_PAR_COUP, chaine.CANAUX,
                     idx=compteur.astype(np.int64))
    evs = []
    for m, te in zip(mks, t_mk):
        e = json.loads(m); det = {"action_id": e["action_id"]}
        if e["type"] == "robot_error_injected":
            det.update(error_kind="mauvais_objet", planned=True)
        evs.append((float(te), 0, e["type"], json.dumps(det), None))
    info = {"agent": "robot", "eeg_device": f"carte UBRI SIMULÉE (trame v{a.trame})", "fs": FS, "unit": "µV",
            "clock_sync": {"method": "dejitter sur compteur de trame + latence calibrée sur bit de synchro",
                           "drift_ppm": round(r["derive_ppm"], 2), "latence_ms": round(r["latence"] * 1e3, 3)},
            "source": "banc_connect.py (données simulées)", "licence": "CC-BY-4.0"}
    ecrire_meta(nrd, info, {"channels": chaine.CANAUX, "electrode_type": "simulée"},
                pd.DataFrame([("sim-01", None, None, True, False, False, "simulation")], columns=PARTICIPANT_COLUMNS),
                pd.DataFrame([(0, "sim-01", 1, 1, float(t_de(i0)), float(t_de(i1)), len(t_cal), rel, i0, i1, pertes)],
                             columns=EPISODE_COLUMNS),
                pd.DataFrame(evs, columns=EVENT_COLUMNS))
    err, cnt = valider(nrd)
    crit["jeu UBRI-NRD valide (v0.2, trous compris)"] = not err
    if err:
        print("NRD :", err[:3])
    print(f"jeu UBRI-NRD : {cnt.get('échantillons manquants')} échantillons manquants, "
          f"{cnt.get('trous (début, milieu, fin)')} trous, {cnt.get('événements dans un trou')} événement(s) dans un trou, "
          f"{cnt.get('pertes non annoncées par la source (côté hôte)')} perte(s) non annoncée(s) par la source (côté hôte)")
    # PAS de critère « manquants du jeu = pertes comptées » : avec idx = compteurs reçus et l'étendue
    # ouverture -> clôture, les deux valent dernier - départ + 1 - reçus (identité, KE#286). La jambe porteuse est
    # le critère « pertes comptées = pertes annoncées par la carte » ci-dessus (cassure --casse-diffuseur => KO) ;
    # le validateur, lui, garde le jeu VALIDE et compte la perte « côté hôte » (manquants > annoncées).
    for k, ok in crit.items():
        print(f"  {'OK' if ok else 'KO'}  {k}")
    work.rename(out)
    tenus = all(crit.values())
    print("CRITÈRES TENUS" if tenus else "CRITÈRES NON TENUS")
    return 0 if tenus else 1


if __name__ == "__main__":
    sys.exit(main())
