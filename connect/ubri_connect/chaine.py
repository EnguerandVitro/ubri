"""Chaîne UBRI Connect : carte (trames UBRI v0 sur un « port série ») -> diffuseur LSL -> enregistreur -> UBRI-NRD.

- carte_simulee() : processus qui écrit des trames UBRI v0 dans un descripteur (tube = port série), échantillonnées
  par un quartz qui dérive, envoyées par paquets avec une latence de transport ; le bit de synchro est levé sur le
  premier échantillon qui suit chaque événement (entrée de synchro câblée, DEV-20). Écrit la VÉRITÉ à la fin.
- diffuseur() : lit les octets, décode, publie un flux LSL (voies en coups ADS1299, bit de synchro, compteur),
  horodaté À L'ARRIVÉE comme un vrai pilote. Il ne connaît pas l'heure réelle des échantillons.
- robot_simule() : publie les marqueurs d'événements avec la latence logicielle du contrôleur.
- traiter() : dejitter par régression horodatage ~ COMPTEUR (juste même si des trames manquent), latence calibrée sur
  les N_CAL premiers bits de synchro, rattachement des événements.
"""
import json
import os
import select
import time
from pathlib import Path

import numpy as np

from . import trame

CANAUX = ["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4"]
UV_PAR_COUP = 4.5 / 24 / (2**23 - 1) * 1e6  # ADS1299, gain 24 (hypothèse de conception de la carte UBRI)


def _ecrire_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def plan_evenements(duree: float, t0: float, periode: float, p_err: float, rng) -> tuple[np.ndarray, list[str]]:
    n = int((duree - 2) / periode)
    t = t0 + 1.0 + periode * np.arange(n) + rng.uniform(0, 0.5, n)
    kinds = ["robot_error_injected" if r < p_err else "robot_correct" for r in rng.random(n)]
    return t, kinds


def carte_simulee(fd: int, t0: float, duree: float, fs: float, drift_ppm: float, paquet: int, lat_base: float,
                  lat_jit: float, t_ev: list[float], verite_path: str, seed: int = 1, version: int = 1,
                  perte: float = 0.0, rafales_drdy: int = 0, compteur: str = "timer", depart: int = 0,
                  rafale_finale: bool = False) -> None:
    """Carte simulée. version 3 = protocole de la carte UBRI : trame d'OUVERTURE, trames v3, trame de CLÔTURE.
    perte : fraction des paquets que la CARTE abandonne (tampon radio plein) ; elle les compte (clôture).
    rafales_drdy : nombre de rafales de 3 DRDY ratées par l'interruption (masquée ~10 ms), tirées hors de ±7
      échantillons des fronts de synchro (limite écrite : la perte d'un front par DRDY ratée n'est pas simulée).
    compteur : « timer » = compteur matériel piloté par DRDY (carte-ubri-v0 §3) : une DRDY ratée fait sauter le
      compteur et la carte la compte ; « isr » = CASSURE, compteur incrémenté par l'interruption : la DRDY ratée
      disparaît sans trace, la carte annonce 0.
    depart : compteur du premier échantillon (annoncé dans l'ouverture ; l'hôte ne doit jamais supposer 0).
    rafale_finale : les 3 DERNIÈRES DRDY de la session sont ratées (cas de queue : la clôture porte la dernière
      valeur du TIMER, pas le compteur de la dernière trame envoyée).
    Les rafales peuvent se chevaucher : le nombre de DRDY ratées est ratee.sum(), jamais 3 × rafales."""
    import pylsl
    assert compteur in ("timer", "isr")
    rng = np.random.default_rng(seed)
    fs_vrai = fs * (1 + drift_ppm * 1e-6)
    n = int(duree * fs_vrai) // paquet * paquet
    t_ech = t0 + np.arange(n) / fs_vrai
    sync_idx = np.searchsorted(t_ech, np.asarray(t_ev))
    fronts = np.sort(sync_idx[sync_idx < n])
    sync = np.zeros(n, bool)
    sync[fronts] = True
    x = np.rint(rng.standard_normal((n, len(CANAUX))) * (10 / UV_PAR_COUP)).astype(int)  # ~10 µV de bruit
    ratee = np.zeros(n, bool)
    if rafales_drdy:
        interdit = np.zeros(n + 3, bool)
        for f in fronts:
            interdit[max(0, f - 7):f + 8] = True
        candidats = [s for s in range(n - 2) if not interdit[s:s + 3].any()]
        for s in rng.choice(candidats, rafales_drdy, replace=False):
            ratee[s:s + 3] = True
    if rafale_finale:
        assert not (sync[n - 3:].any()), "front de synchro dans la rafale finale"
        ratee[n - 3:] = True
    servis = np.where(~ratee)[0]
    num = depart + (servis if compteur == "timer" else np.arange(len(servis)))  # compteur PORTÉ par chaque trame
    num_de = dict(zip(servis.tolist(), num.tolist()))
    # dernier front (en compteur porté) au moment de chaque échantillon servi ; AUCUN_FRONT avant le premier
    k = np.searchsorted(fronts, servis, side="right") - 1
    dernier = np.full(len(servis), trame.AUCUN_FRONT, dtype=np.int64)
    dernier[k >= 0] = [num_de[int(f)] for f in fronts[k[k >= 0]]]

    def tr(j: int) -> bytes:
        s = int(servis[j])
        if version == 3:
            return trame.encoder(int(num[j]), x[s].tolist(), bool(sync[s]), int(dernier[j]), (0, 0), 200)
        return trame.encoder(int(num[j]), x[s].tolist(), bool(sync[s]), None if version == 1 else int(dernier[j]))

    if version == 3:
        os.write(fd, trame.encoder_ouverture(CANAUX, depart, int(fs), 24, 0x0B1, 0))
    abandonnees = 0
    for a in range(0, len(servis), paquet):
        b = min(a + paquet, len(servis))
        attente = t_ech[servis[b - 1]] + lat_base + rng.uniform(0, lat_jit) - pylsl.local_clock()
        if attente > 0:
            time.sleep(attente)
        if perte > 0 and rng.random() < perte:
            abandonnees += b - a
            continue
        os.write(fd, b"".join(tr(j) for j in range(a, b)))
    drdy_vues = int(ratee.sum()) if compteur == "timer" else 0
    dernier_emis = depart + n - 1 if compteur == "timer" else int(num[-1])  # le TIMER a compté toutes les DRDY
    if version == 3:
        os.write(fd, trame.encoder_cloture(len(CANAUX), dernier_emis, drdy_vues, abandonnees))
    os.close(fd)
    _ecrire_json(Path(verite_path), {"t_ech0": float(t_ech[0]), "fs_vrai": fs_vrai, "n": int(n),
                                     "sync_idx": sync_idx.tolist(), "indices_servis": servis.tolist(),
                                     "compteurs_servis": num.tolist(), "drdy_ratees": int(ratee.sum()),
                                     "trames_abandonnees": abandonnees, "depart": depart, "compteur": compteur})


def diffuseur(fd: int, tag: str, fs: float, fin_monotone: float, casse_jeter: int | None = None, *,
              canaux: list[str] | None = None) -> bool:
    """Lit le « port série » jusqu'à sa fermeture (ou la borne dure), publie sur LSL.
    Les trames d'une même lecture sont poussées par segments CONTIGUS de compteur : LSL date les échantillons
    antérieurs du paquet au pas nominal, un saut de compteur dans un même paquet décalerait leur horodatage.
    casse_jeter : CASSURE de banc, la trame de ce compteur est jetée après décodage (perte côté hôte).
    canaux : libellés des voies publiées (défaut CANAUX, la carte simulée) ; la carte UBRI v0 en envoie 8 (EOG
    compris) : un nombre de voies différent du flux ferait refuser chaque échantillon par LSL.
    Un second flux LSL « session » (créé AVANT toute donnée, pour que l'enregistreur y soit connecté) porte à la
    fermeture du port : ouverture, clôture, incohérences du décodeur. L'enregistreur en tire les pertes de tête et
    de queue sans connaître la vérité ; sans ce message, il ne conclut rien (borne, échec bruyant).
    Rend True SEULEMENT si ce bilan a été publié (fin du port reçue) ; False si la borne dure l'a arrêté avant : le
    récepteur Bluetooth s'y fie, « encore vivant » ne prouve rien pendant la seconde de vidage (KE#293)."""
    import dataclasses
    import pylsl
    sess = pylsl.StreamOutlet(pylsl.StreamInfo(f"ubri-session-{tag}", "Markers", 1, 0, "string", f"session-{tag}"))
    canaux = CANAUX if canaux is None else list(canaux)
    info = pylsl.StreamInfo(f"ubri-eeg-{tag}", "EEG", len(canaux) + 3, fs, "double64", f"eeg-{tag}")
    ch = info.desc().append_child("channels")
    for c in [*canaux, "front", "synchro", "compteur"]:  # front = -1 si inconnu (trame v1 ou aucun front)
        ch.append_child("channel").append_child_value("label", c)
    info.desc().append_child_value("uv_par_coup", repr(UV_PAR_COUP))
    out = pylsl.StreamOutlet(info, 10)
    dec = trame.Decodeur()
    bilan = False
    while time.monotonic() < fin_monotone:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            continue
        data = os.read(fd, 65536)
        if not data:
            sess.push_sample([json.dumps({
                "ouverture": dataclasses.asdict(dec.ouverture) if dec.ouverture else None,
                "cloture": dataclasses.asdict(dec.cloture) if dec.cloture else None,
                "incoherences": dec.incoherences, "incoherences_total": dec.incoherences_total, "rejets_crc": dec.rejets_crc,
                "rejets_domaine": dec.rejets_domaine, "perdus_decodeur": dec.perdus_total})])
            bilan = True
            break  # port fermé : fin de session
        tr = [t for t in dec.nourrir(data) if t.compteur != casse_jeter]
        if dec.ouverture is not None and len(dec.ouverture.voies) != len(canaux):
            # échec BRUYANT : push_chunk aplatit les listes, 11 échantillons de 10 valeurs passeraient mal découpés
            raise ValueError(f"ouverture à {len(dec.ouverture.voies)} voies, flux LSL à {len(canaux)}")
        if not tr:
            continue
        maintenant = pylsl.local_clock()
        coupes = [0] + [k for k in range(1, len(tr)) if tr[k].compteur != tr[k - 1].compteur + 1] + [len(tr)]
        for a, b in zip(coupes, coupes[1:]):
            seg = tr[a:b]
            # le dernier échantillon du segment est daté « maintenant » moins ce qui le sépare du dernier reçu
            t_seg = maintenant - (tr[-1].compteur - seg[-1].compteur) / fs
            out.push_chunk([[*t.voies, float(-1 if t.front is None else t.front), float(t.synchro), float(t.compteur)]
                            for t in seg], t_seg)
    time.sleep(1.0)  # laisse l'enregistreur vider
    os.close(fd)
    return bilan


def robot_simule(tag: str, t_ev: list[float], kinds: list[str], mk_lat: float, mk_jit: float, fin_monotone: float,
                 seed: int = 2) -> None:
    import pylsl
    rng = np.random.default_rng(seed)
    out = pylsl.StreamOutlet(pylsl.StreamInfo(f"ubri-events-{tag}", "Markers", 1, 0, "string", f"events-{tag}"))
    for i, (t, k) in enumerate(zip(t_ev, kinds)):
        ts = t + mk_lat + rng.uniform(0, mk_jit)
        while pylsl.local_clock() < ts and time.monotonic() < fin_monotone:
            time.sleep(0.0005)
        out.push_sample([json.dumps({"type": k, "action_id": i})], ts)
    time.sleep(2.0)


def traiter(t_raw: np.ndarray, compteur: np.ndarray, sync: np.ndarray, t_mk: np.ndarray, n_cal: int,
            fs: float, front: np.ndarray | None = None) -> dict:
    """Ne voit que ce que l'enregistreur a reçu. Les ancres sont des COMPTEURS (heure = a0 + pas × compteur, valable
    même pour une trame jamais reçue) : bits de synchro reçus, plus les fronts répétés par les trames v2.
    Appariement événement -> ancre PAR LE TEMPS (jamais par l'ordre), à moins de 1,5 échantillon de la position
    prédite par la latence grossière ; sinon « ancre perdue » (-1), jamais une autre ancre.
    Latence fine = moyenne sur les n_cal premières paires, moins le demi-pas (le front tombe sur le premier
    échantillon APRÈS l'événement)."""
    b, a0 = np.polyfit(compteur, t_raw, 1)  # dejitter sur le compteur : les trames perdues ne faussent rien
    ancres = set(compteur[sync > 0.5].astype(np.int64).tolist())
    if front is not None:
        ancres |= set(int(f) for f in front if f >= 0)
    ancres = np.array(sorted(ancres), dtype=np.int64)
    ta = a0 + b * ancres
    ecarts = []
    for t in t_mk:  # ancre la plus proche dans ±100 ms (la latence des marqueurs peut dépasser celle de l'EEG)
        k = int(np.argmin(np.abs(ta - t)))
        if abs(ta[k] - t) < 0.1:
            ecarts.append(ta[k] - t)
    if not ecarts:
        raise ValueError("aucune ancre dans les 100 ms d'aucun événement")
    grossiere = float(np.median(ecarts))
    paire = np.full(len(t_mk), -1, dtype=np.int64)
    pris = set()
    for i, t in enumerate(t_mk):
        k = int(np.argmin(np.abs(ta - (t + grossiere))))
        if abs(ta[k] - (t + grossiere)) < 1.5 / fs and k not in pris:
            paire[i] = ancres[k]; pris.add(k)
    ok = np.where(paire >= 0)[0]
    if len(ok) < n_cal:
        raise ValueError(f"seulement {len(ok)} événements appariés, {n_cal} requis pour calibrer")
    cal = ok[:n_cal]
    latence = float(np.mean(a0 + b * paire[cal] - t_mk[cal]) - b / 2)
    return {"a0": float(a0), "pas": float(b), "latence": latence, "t_cal": a0 + b * compteur - latence,
            "paire": paire, "cal": cal, "derive_ppm": float((1 / b / fs - 1) * 1e6),
            "ancres_perdues": int((paire < 0).sum())}
