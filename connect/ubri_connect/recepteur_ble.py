"""Récepteur Bluetooth de la carte UBRI v0 (nRF52840), côté poste d'acquisition : notifications FLUX -> tube ->
chaine.diffuseur (le MÊME diffuseur LSL que le banc de bout en bout, déjà testé) -> flux LSL eeg + session.

Pourquoi un tube : le diffuseur lit un « port série » jusqu'à sa fermeture et publie alors le bilan de session
(ouverture, clôture, pertes). Le récepteur ne décode donc rien pour LSL ; il écrit les octets reçus dans le tube et
le ferme à la fin. Un second décodeur, local, ne sert qu'à deux gardes :
- l'OUVERTURE annonce les voies attendues (8, EOG compris) : sinon la session est arrêtée (code 4), car un flux LSL
  à 8 colonnes recevrait des échantillons d'une autre forme ;
- après la commande « fermer », on ATTEND la clôture (au plus ATTENTE_CLOTURE_S) : sans elle, la queue de session
  n'est pas comptable (perdus_total = None, jamais 0) et le code de sortie le dit (3).

Contrat du microprogramme (products/ubri/firmware/app/src/main.c) : chaque notification porte des trames ENTIÈRES
d'une même session, dans l'ordre ; COMMANDE 0x01 ouvre, 0x02 ferme ; la carte exige L4 (LE Secure Connections
authentifié) avant toute commande ou abonnement : au premier accès, le système d'exploitation demande la clé à six
chiffres affichée sur la console de la carte (fenêtre d'appairage, SANS électrode posée, KE#285).

Codes de sortie : 0 session bornée (ouverture et clôture reçues), 2 carte introuvable, 3 clôture non reçue
(déconnexion, ou carte muette), 4 ouverture absente ou non conforme, 5 bibliothèque bleak absente,
6 diffuseur LSL arrêté (exception, borne atteinte ou octets non transmis : rien ou partie seulement publié, la
session ne vaut rien quoi que dise la carte), 7 erreur Bluetooth (appairage refusé, liaison perdue à l'établissement).
Une clôture reçue SANS commande « fermer » (la carte a clos d'elle-même : ADS1299 muette 2 s) termine la session.
Jamais exécuté contre une vraie carte (ce serveur n'a pas de radio) : testé avec un faux client
(tests/test_recepteur_ble.py)."""
import argparse
import asyncio
import os
import sys
import threading
import time

from . import chaine, trame

UUID_SERVICE = "6a1b0000-5b3c-4e0f-9d2a-7c1e0a5f3b21"
UUID_COMMANDE = "6a1b0001-5b3c-4e0f-9d2a-7c1e0a5f3b21"
UUID_FLUX = "6a1b0002-5b3c-4e0f-9d2a-7c1e0a5f3b21"
OUVRIR, FERMER = b"\x01", b"\x02"
VOIES_CARTE = list(trame.LIBELLES[:8])  # codes 0..7 envoyés par main.c : Fz FCz Cz CPz Pz C3 C4 EOG
FS_CARTE = 250
# Microprogramme : la boucle d'acquisition voit « fermer » en <= 100 ms, la clôture attend au plus 200 ms une place
# dans la file, puis part au prochain intervalle de connexion (<= 50 ms) : < 0,4 s attendues, 3 s laissées.
# À MESURER au banc (essai 7 de firmware/app/README.md).
ATTENTE_CLOTURE_S = 3.0
ATTENTE_OUVERTURE_S = 3.0  # même chemin que la clôture, depuis « ouvrir »
MARGE_DIFFUSEUR_S = 10.0  # borne dure du diffuseur = session + attentes + cette marge


class Session:
    """État partagé entre le rappel de notification (boucle asyncio) et la fin de session."""

    def __init__(self, fd_ecriture: int | None, deconnectee: asyncio.Event):
        self.fd = fd_ecriture
        self.garde = trame.Decodeur()
        self.ouverture_vue = asyncio.Event()
        self.cloture_vue = asyncio.Event()
        self.deconnectee = deconnectee
        self.octets = 0
        self.octets_non_transmis = 0  # tube plein ou fermé : perte côté hôte, que le décodeur du diffuseur verra

    def notification(self, _car, donnees: bytearray) -> None:
        b = bytes(donnees)
        self.octets += len(b)
        if self.fd is None:  # notification tardive, après la fermeture du tube : comptée, jamais écrite
            self.octets_non_transmis += len(b)
            return
        try:
            os.write(self.fd, b)
        except (BlockingIOError, BrokenPipeError) as e:
            # jamais bloquer la boucle radio sur un diffuseur arrêté : on compte, le trou de compteur dira le reste
            self.octets_non_transmis += len(b)
            if self.octets_non_transmis == len(b):
                print(f"recepteur_ble : tube vers le diffuseur {type(e).__name__}, octets comptés comme perdus",
                      file=sys.stderr)
        self.garde.nourrir(b)
        if self.garde.ouverture is not None:
            self.ouverture_vue.set()
        if self.garde.cloture is not None:
            self.cloture_vue.set()


async def _attendre(ev: asyncio.Event, autre: asyncio.Event, delai: float) -> bool:
    """True si `ev` arrive dans le délai ; False sur délai dépassé OU sur `autre` (déconnexion) avant lui."""
    t_ev, t_autre = asyncio.ensure_future(ev.wait()), asyncio.ensure_future(autre.wait())
    try:
        await asyncio.wait({t_ev, t_autre}, timeout=delai, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t_ev, t_autre):
            t.cancel()
    return ev.is_set()


def ouverture_conforme(o: trame.Ouverture | None) -> str | None:
    """None si l'ouverture correspond à ce que le flux LSL publie ; sinon la raison, nommée."""
    if o is None:
        return "ouverture non reçue"
    if list(o.voies) != VOIES_CARTE:
        return f"voies {list(o.voies)} au lieu de {VOIES_CARTE}"
    if o.fs != FS_CARTE:
        return f"fs {o.fs} Hz au lieu de {FS_CARTE}"
    return None


async def session(client, duree: float, tag: str, deconnectee: asyncio.Event, *, diffuseur=None) -> int:
    """Une session complète sur un client DÉJÀ connecté (BleakClient ou faux client de test). `deconnectee` est
    posé par le rappel de déconnexion du client. `diffuseur(fd, tag, fs, fin_monotone, canaux=...)` :
    chaine.diffuseur par défaut (pylsl requis).
    Le diffuseur n'a réussi que s'il RAPPORTE avoir publié son bilan (il rend True, ce qu'il ne peut faire qu'à la
    fin du tube, donc après notre fermeture) et qu'aucun octet ne lui a manqué : une sortie à sa borne dure, sans
    exception, laisse un bilan absent ou partiel (KE#293) => code 6. « Encore vivant » ne prouverait rien :
    chaine.diffuseur dort 1 s après sa boucle."""
    diffuseur = diffuseur or chaine.diffuseur
    r, w = os.pipe()
    os.set_blocking(w, False)
    s = Session(w, deconnectee)
    panne: list[str] = []
    bilan: list[bool] = []
    fil = None

    def cible(fin: float) -> None:
        try:
            bilan.append(diffuseur(r, tag, float(FS_CARTE), fin, canaux=VOIES_CARTE) is True)
        except Exception as e:  # noqa: BLE001  nommée par son type, jamais avalée : code 6
            panne.append(type(e).__name__)
            try:
                os.close(r)
            except OSError:
                pass

    code = 0
    try:
        await client.start_notify(UUID_FLUX, s.notification)
        # borne dure APRÈS l'abonnement : sous macOS, start_notify dure le temps de saisir la clé (jusqu'à 30 s, la
        # carte coupe ensuite) ; les octets reçus entre-temps attendent dans le tube (KE#293)
        fin = time.monotonic() + duree + ATTENTE_OUVERTURE_S + ATTENTE_CLOTURE_S + MARGE_DIFFUSEUR_S
        fil = threading.Thread(target=cible, args=(fin,), name="diffuseur", daemon=False)
        fil.start()
        await client.write_gatt_char(UUID_COMMANDE, OUVRIR, response=True)
        if not await _attendre(s.ouverture_vue, s.deconnectee, ATTENTE_OUVERTURE_S):
            print("recepteur_ble : ouverture non reçue", file=sys.stderr)
            code = 4
        elif (raison := ouverture_conforme(s.garde.ouverture)) is not None:
            print(f"recepteur_ble : ouverture non conforme ({raison}) : session arrêtée", file=sys.stderr)
            code = 4
        else:
            fin_session = time.monotonic() + duree
            while (time.monotonic() < fin_session and not s.deconnectee.is_set() and not s.cloture_vue.is_set()
                   and fil.is_alive()):
                await _attendre(s.cloture_vue, s.deconnectee, min(0.5, max(0.0, fin_session - time.monotonic())))
            if s.cloture_vue.is_set():
                print("recepteur_ble : la carte a clos la session d'elle-même (ADS1299 muette ?)", file=sys.stderr)
        if not s.deconnectee.is_set() and not s.cloture_vue.is_set():
            await client.write_gatt_char(UUID_COMMANDE, FERMER, response=True)
            if not await _attendre(s.cloture_vue, s.deconnectee, ATTENTE_CLOTURE_S) and code == 0:
                code = 3
        elif code == 0 and not s.cloture_vue.is_set():
            code = 3  # déconnexion avant la clôture : la carte ferme sans pouvoir l'envoyer (session non bornée)
        if not s.deconnectee.is_set():
            await client.stop_notify(UUID_FLUX)
    finally:
        s.fd = None  # plus aucune écriture : un descripteur fermé peut être réattribué
        os.close(w)  # fin du « port » : le diffuseur publie le bilan de session puis se termine
        if fil is not None:
            await asyncio.to_thread(fil.join)  # borné par `fin` ; hors de la boucle d'événements
        else:
            os.close(r)  # échec avant le démarrage du diffuseur : personne ne fermera ce bout
    bilan_publie = bilan == [True]  # une exception (panne) empêche aussi le bilan : `panne` ne sert qu'à NOMMER la cause
    if panne or not bilan_publie or s.octets_non_transmis:
        cause = (f"exception {panne[0]}" if panne else "bilan non publié (borne atteinte avant la fin du tube)"
                 if not bilan_publie else f"{s.octets_non_transmis} octets non transmis")
        print(f"recepteur_ble : diffuseur LSL ({cause}) : bilan absent ou partiel, session inutilisable",
              file=sys.stderr)
        code = code if code == 4 else 6  # une ouverture non conforme fait aussi refuser le diffuseur : 4 la nomme
    if code == 3:
        print("recepteur_ble : clôture non reçue, session non bornée (perdus_total = None)", file=sys.stderr)
    print(f"recepteur_ble : {s.octets} octets reçus, {s.octets_non_transmis} non transmis au diffuseur, "
          f"pertes vues : {s.garde.perdus_total}, incohérences : {s.garde.incoherences_total}", file=sys.stderr)
    return code


async def _principal(a) -> int:
    try:
        from bleak import BleakClient, BleakScanner
        from bleak.exc import BleakError
    except ImportError:
        print("recepteur_ble : bibliothèque bleak absente (pip install bleak)", file=sys.stderr)
        return 5
    try:
        return await _avec_bleak(a, BleakClient, BleakScanner)
    except (BleakError, asyncio.TimeoutError) as e:
        # appairage refusé (hors fenêtre), carte coupée à 30 s, liaison perdue pendant l'établissement
        print(f"recepteur_ble : erreur Bluetooth {type(e).__name__} (appairage refusé ? fenêtre fermée ?)",
              file=sys.stderr)
        return 7


async def _avec_bleak(a, BleakClient, BleakScanner) -> int:
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: UUID_SERVICE in [u.lower() for u in ad.service_uuids] and (a.adresse is None or
                                                                                d.address == a.adresse),
        timeout=a.recherche, service_uuids=[UUID_SERVICE])
    if dev is None:
        print(f"recepteur_ble : aucune carte UBRI annoncée en {a.recherche} s", file=sys.stderr)
        return 2
    print(f"recepteur_ble : carte {dev.address}", file=sys.stderr)
    boucle, deconnectee = asyncio.get_running_loop(), asyncio.Event()
    # bleak peut appeler ce rappel hors de la boucle : on repasse par elle
    async with BleakClient(dev, disconnected_callback=lambda _c: boucle.call_soon_threadsafe(deconnectee.set)) as client:
        if a.appairer:
            try:
                await client.pair()  # Linux/Windows ; sous macOS, le système demande la clé au premier accès
            except NotImplementedError:
                pass
        return await session(client, a.duree, a.tag, deconnectee)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--duree", type=float, default=60.0, help="durée de la session, en secondes")
    ap.add_argument("--tag", default="ubri", help="suffixe des flux LSL (ubri-eeg-<tag>, ubri-session-<tag>)")
    ap.add_argument("--adresse", default=None, help="adresse (ou UUID sous macOS) d'une carte précise")
    ap.add_argument("--recherche", type=float, default=10.0, help="durée de la recherche, en secondes")
    ap.add_argument("--appairer", action="store_true", help="demander l'appairage explicitement (Linux, Windows)")
    return asyncio.run(_principal(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
