"""Tests du récepteur Bluetooth (sans radio : faux client qui se comporte comme le microprogramme main.c).
Lancer : python3 -B tests/test_recepteur_ble.py   (sans dépendance ; le test LSL tourne si pylsl est importable,
sinon il est annoncé SAUTÉ, jamais compté comme réussi).

Le faux client reproduit le contrat de main.c : paquets de trames ENTIÈRES d'une même session (≤ 244 octets),
ouverture puis échantillons v3 à 8 voies puis clôture ; un paquet dont la notification échoue est compté par la
carte en « abandonnées » et annoncé dans la clôture. Le critère « pertes vues = pertes annoncées » n'est pas une
identité : les pertes vues (dernier émis − départ + 1 − trames reçues, bornes annoncées par la carte) sortent du
décodeur sur les octets REÇUS, les annoncées d'un compteur séparé du faux microprogramme. Il mesure la FIDÉLITÉ du
récepteur (rien perdu ni dupliqué côté hôte), pas la comptabilité du microprogramme (KE#286)."""
import asyncio
import fcntl
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106
from ubri_connect import recepteur_ble as R  # noqa: E402
from ubri_connect import trame as T  # noqa: E402

DEPART = 1000
UTILE = 244


class FauxClient:
    """Carte simulée derrière l'interface BleakClient utilisée par recepteur_ble.session."""

    def __init__(self, deconnectee, *, voies=R.VOIES_CARTE, fs=R.FS_CARTE, paquets_perdus=(), couper_apres=None,
                 delai_cloture=0.2, sans_cloture=False, couper_apres_cloture=False, auto_cloture_apres=None,
                 muet_apres=None):
        self.deconnectee, self.voies, self.fs = deconnectee, voies, fs
        self.paquets_perdus, self.couper_apres = set(paquets_perdus), couper_apres
        self.delai_cloture, self.sans_cloture = delai_cloture, sans_cloture
        self.couper_apres_cloture, self.auto_cloture_apres = couper_apres_cloture, auto_cloture_apres
        self.muet_apres = muet_apres
        self.rappel = None
        self.tache = None
        self.compteur = DEPART
        self.abandonnees = 0
        self.paquet_no = 0
        self.file = []
        self.commandes = []

    async def start_notify(self, uuid, rappel):
        assert uuid == R.UUID_FLUX
        self.rappel = rappel

    async def stop_notify(self, uuid):
        assert uuid == R.UUID_FLUX

    def _envoyer(self):
        """Regroupe la file en paquets de trames entières, comme fil_ble de main.c."""
        while self.file:
            paquet, nb_ech = b"", 0
            while self.file and len(paquet) + len(self.file[0][0]) <= UTILE:
                t, ech = self.file.pop(0)
                paquet += t
                nb_ech += ech
            self.paquet_no += 1
            if self.paquet_no in self.paquets_perdus:
                self.abandonnees += nb_ech  # notification échouée : la carte le sait et l'annonce
            else:
                self.rappel(None, bytearray(paquet))

    async def _acquisition(self):
        self.file.append((T.encoder_ouverture(self.voies, DEPART, self.fs, 24, 0x0B1, 1), 0))
        self._envoyer()
        k = 0
        while not self.deconnectee.is_set():
            if self.muet_apres is not None and k >= self.muet_apres:
                await asyncio.sleep(0.02)  # connectée mais muette : plus aucun octet
                continue
            for _ in range(5):  # 5 échantillons par « intervalle de connexion » de 20 ms à 250 Hz
                v = [((self.compteur * 7 + i * 13) % 2001) - 1000 for i in range(len(self.voies))]
                self.file.append((T.encoder(self.compteur, v, False, None, (0, 0), 200), 1))
                self.compteur += 1
            self._envoyer()
            k += 1
            if self.auto_cloture_apres is not None and k >= self.auto_cloture_apres:
                await self._cloturer()  # ADS1299 muette : la carte clôt d'elle-même, sans commande
                return
            if self.couper_apres is not None and k >= self.couper_apres:
                self.deconnectee.set()  # la carte disparaît : pas de clôture
                return
            await asyncio.sleep(0.02)

    async def write_gatt_char(self, uuid, donnees, response=True):
        assert uuid == R.UUID_COMMANDE and response
        self.commandes.append(bytes(donnees))
        if donnees == R.OUVRIR:
            self.tache = asyncio.ensure_future(self._acquisition())
        elif donnees == R.FERMER:
            if self.tache is not None:
                self.tache.cancel()
            # la réponse d'écriture revient TOUT DE SUITE ; la clôture arrive plus tard, en notification (main.c)
            self.tache_cloture = asyncio.ensure_future(self._cloturer())

    async def _cloturer(self):
        await asyncio.sleep(self.delai_cloture)
        if not self.sans_cloture and not self.deconnectee.is_set():
            self.file.append((T.encoder_cloture(len(self.voies), self.compteur - 1, 0, self.abandonnees), 0))
            self._envoyer()
            if self.couper_apres_cloture:
                self.deconnectee.set()  # la liaison tombe juste après la clôture : session quand même bornée


def faux_diffuseur(resultat):
    """Lit le tube jusqu'à sa fermeture et décode, comme chaine.diffuseur (sans LSL)."""
    def d(fd, tag, fs, fin, *, canaux):
        dec = T.Decodeur()
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            dec.nourrir(b)
        os.close(fd)
        resultat.update(dec=dec, canaux=canaux, fs=fs)
        return True  # bilan « publié » : fin du tube reçue
    return d


def lancer(duree=0.5, **kw):
    async def corps():
        deco = asyncio.Event()
        client = FauxClient(deco, **kw)
        res = {}
        code = await R.session(client, duree, "test", deco, diffuseur=faux_diffuseur(res))
        return code, res, client
    return asyncio.run(corps())


def test_nominal_avec_pertes_annoncees():
    code, res, c = lancer(paquets_perdus={3, 7})
    dec = res["dec"]
    assert code == 0, code
    assert c.commandes == [R.OUVRIR, R.FERMER]
    assert c.abandonnees > 0  # le scénario produit bien des pertes (sinon le critère serait vide, KE#111)
    assert dec.cloture is not None and dec.cloture.abandonnees == c.abandonnees
    # UN contrôle (KE#111) : avec dernier_emis = compteur − 1, « perdus_total = abandonnées » équivaut à « tout
    # échantillon émis est reçu OU annoncé » (trames + abandonnées = émis) ; on n'en écrit qu'un
    assert dec.perdus_total == c.abandonnees, (dec.perdus_total, c.abandonnees)
    assert res["canaux"] == R.VOIES_CARTE and res["fs"] == 250.0
    print(f"OK  session bornée : {dec.trames} trames reçues, {c.abandonnees} perdues = annoncées, code 0")


def test_deconnexion_sans_cloture():
    code, res, _ = lancer(duree=5.0, couper_apres=10)
    assert code == 3, code
    assert res["dec"].cloture is None and res["dec"].perdus_total is None  # jamais 0 (KE#105)
    print("OK  déconnexion en pleine session : code 3, perdus_total None")


def test_cloture_trop_tardive():
    code, res, _ = lancer(delai_cloture=R.ATTENTE_CLOTURE_S + 0.5)
    assert code == 3, code
    print("OK  clôture après le délai : code 3")


def test_cloture_absente():
    code, res, _ = lancer(sans_cloture=True)
    assert code == 3 and res["dec"].perdus_total is None, code
    print("OK  carte qui n'envoie pas la clôture : code 3")


def test_deconnexion_apres_cloture():
    # la carte clôt d'elle-même puis la liaison tombe : la session est BORNÉE (code 0), sans commande « fermer »
    code, res, c = lancer(duree=30.0, auto_cloture_apres=8, couper_apres_cloture=True, paquets_perdus={2})
    assert code == 0, code
    assert res["dec"].perdus_total == res["dec"].cloture.abandonnees == c.abandonnees > 0
    assert c.commandes == [R.OUVRIR], c.commandes
    print("OK  clôture spontanée puis déconnexion : session bornée, code 0, sans attendre la durée prévue")


def test_diffuseur_en_panne():
    async def corps():
        deco = asyncio.Event()

        def casse(fd, tag, fs, fin, *, canaux):
            os.read(fd, 10)
            raise ValueError("panne simulée")
        return await R.session(FauxClient(deco), 30.0, "test", deco, diffuseur=casse)
    assert asyncio.run(corps()) == 6
    print("OK  diffuseur LSL en panne : code 6, session arrêtée sans attendre la durée prévue")


class SaisieLente(FauxClient):
    """macOS : start_notify dure le temps de saisir la clé à six chiffres."""
    saisie = 0.0

    async def start_notify(self, uuid, rappel):
        await asyncio.sleep(self.saisie)
        await super().start_notify(uuid, rappel)


def diffuseur_borne(etat, vidage=0.0):
    """Même structure que chaine.diffuseur : borne dure, bilan publié SEULEMENT à la fin du tube, puis `vidage`
    secondes de sommeil (chaine.diffuseur : 1 s) pendant lesquelles il est encore vivant."""
    def d(fd, tag, fs, fin, *, canaux):
        import select
        dec, bilan = T.Decodeur(), False
        while time.monotonic() < fin:
            if not select.select([fd], [], [], 0.05)[0]:
                continue
            b = os.read(fd, 65536)
            if not b:
                bilan = True
                break
            dec.nourrir(b)
        etat.update(bilan=bilan, trames=dec.trames)
        time.sleep(vidage)
        os.close(fd)
        return bilan
    return d


def _avec_marge(marge, corps):
    ancienne = R.MARGE_DIFFUSEUR_S
    R.MARGE_DIFFUSEUR_S = marge
    try:
        return asyncio.run(corps())
    finally:
        R.MARGE_DIFFUSEUR_S = ancienne


def test_saisie_de_cle_lente():
    # saisie plus longue que (session + attentes + marge) : la borne du diffuseur part APRÈS l'abonnement (KE#293)
    etat = {}

    async def corps():
        deco = asyncio.Event()
        c = SaisieLente(deco)
        c.saisie = 0.5 + R.ATTENTE_OUVERTURE_S + R.ATTENTE_CLOTURE_S + 0.5 + 1.0
        return await R.session(c, 0.5, "test", deco, diffuseur=diffuseur_borne(etat))
    code = _avec_marge(0.5, corps)
    assert code == 0 and etat["bilan"] and etat["trames"] > 0, (code, etat)
    print(f"OK  saisie de clé plus longue que la borne : bilan publié, {etat['trames']} trames, code 0")


def test_diffuseur_borne_atteinte():
    # le diffuseur sort à SA borne, sans exception, avant la fin de la session : jamais un succès (KE#293)
    etat = {}

    async def corps():
        deco = asyncio.Event()
        return await R.session(FauxClient(deco), 3.0, "test", deco, diffuseur=diffuseur_borne(etat))
    code = _avec_marge(-(3.0 + R.ATTENTE_OUVERTURE_S + R.ATTENTE_CLOTURE_S) + 1.0, corps)
    assert code == 6 and not etat["bilan"], (code, etat)
    print("OK  diffuseur sorti à sa borne sans exception : code 6, pas « session bornée »")


def test_diffuseur_borne_sans_octet_perdu():
    # sans octet perdu (KE#76 : la jambe « octets non transmis » ne peut pas sauver ce cas) : la carte se tait avant
    # la borne du diffuseur ; seul « bilan non publié » peut donner 6 (sans lui : 3, clôture absente)
    etat = {}

    async def corps():
        deco = asyncio.Event()
        c = FauxClient(deco, muet_apres=5, sans_cloture=True)
        code = await R.session(c, 3.0, "test", deco, diffuseur=diffuseur_borne(etat))
        return code, c
    code, _ = _avec_marge(-(3.0 + R.ATTENTE_OUVERTURE_S + R.ATTENTE_CLOTURE_S) + 1.0, corps)
    assert code == 6 and not etat["bilan"], (code, etat)
    print("OK  diffuseur sorti à sa borne, aucun octet perdu : code 6 (la jambe « bilan » seule)")


def test_diffuseur_borne_encore_vivant():
    # jambe « bilan publié » SEULE (KE#76) : borne atteinte, mais le diffuseur dort encore (vidage) au moment où l'on
    # ferme le tube ; la clôture arrive dans le tube (aucun octet perdu) => sans cette jambe : code 0, bilan absent
    etat = {}

    async def corps():
        deco = asyncio.Event()
        c = FauxClient(deco, muet_apres=5)
        return await R.session(c, 3.0, "test", deco, diffuseur=diffuseur_borne(etat, vidage=5.0))
    code = _avec_marge(-(3.0 + R.ATTENTE_OUVERTURE_S + R.ATTENTE_CLOTURE_S) + 1.0, corps)
    assert code == 6 and not etat["bilan"], (code, etat)
    print("OK  borne atteinte, diffuseur encore vivant, aucun octet perdu : code 6 (la jambe « bilan » seule)")


def test_tube_plein():
    # diffuseur vivant qui ne lit plus (machine chargée) : le tube déborde, les octets manquent => code 6
    if not hasattr(fcntl, "F_SETPIPE_SZ"):
        print("SAUTÉ  tube plein : F_SETPIPE_SZ absent (Linux seulement)")
        return
    res = {}

    def lent(fd, tag, fs, fin, *, canaux):
        fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, 4096)
        time.sleep(1.5)  # ~15 ko émis pendant ce temps : le tube de 4 ko déborde
        return faux_diffuseur(res)(fd, tag, fs, fin, canaux=canaux)

    async def corps():
        deco = asyncio.Event()
        return await R.session(FauxClient(deco), 2.0, "test", deco, diffuseur=lent)
    code = asyncio.run(corps())
    assert code == 6 and res["dec"].perdus > 0, (code, res)
    print(f"OK  tube plein : octets non transmis, {res['dec'].perdus} trames perdues côté hôte, code 6")


def test_ouverture_non_conforme():
    code, _, c = lancer(voies=R.VOIES_CARTE[:7])
    assert code == 4 and c.commandes == [R.OUVRIR, R.FERMER], (code, c.commandes)
    code, _, _ = lancer(fs=500)
    assert code == 4, code
    print("OK  ouverture à 7 voies ou à 500 Hz : session arrêtée, code 4")


def test_lsl_reel():
    """Le VRAI chaine.diffuseur avec les 8 voies de la carte : flux à 11 colonnes, bilan de session."""
    try:
        import pylsl
    except ImportError:
        print("SAUTÉ  test LSL : pylsl absent (lancer avec ../nrd/.venv/bin/python et PYLSL_LIB)")
        return
    import json
    import threading

    tag = f"t{os.getpid()}"
    sortie = {}

    def lire():
        r = pylsl.resolve_byprop("source_id", f"session-{tag}", 1, 10.0)
        e = pylsl.resolve_byprop("source_id", f"eeg-{tag}", 1, 10.0)
        assert r and e, "flux LSL introuvables"
        sortie["colonnes"] = e[0].channel_count()
        i = pylsl.StreamInlet(r[0])
        i.open_stream(10.0)  # sinon ouvert au premier pull : le bilan poussé avant serait perdu
        s, _ = i.pull_sample(timeout=20.0)
        sortie["session"] = json.loads(s[0]) if s else None

    th = threading.Thread(target=lire)
    th.start()

    async def corps():
        deco = asyncio.Event()
        client = FauxClient(deco, paquets_perdus={4})
        return await R.session(client, 4.0, tag, deco), client  # 4 s : l'inlet de session s'ouvre avant la fin
    code, c = asyncio.run(corps())
    th.join(30.0)
    assert code == 0 and sortie.get("colonnes") == 8 + 3, (code, sortie)  # le code d'abord : il nomme la panne
    s = sortie.get("session")
    assert s is not None and s["perdus_decodeur"] == c.abandonnees > 0, (s, c.abandonnees)
    assert s["ouverture"]["voies"] == R.VOIES_CARTE and not s["incoherences"]
    print(f"OK  LSL réel : flux à 11 colonnes, bilan publié, pertes {s['perdus_decodeur']} = annoncées")

    # ouverture à 7 voies avec le VRAI diffuseur : le récepteur la nomme (4), pas « diffuseur en panne » (6)
    async def non_conforme():
        deco = asyncio.Event()
        return await R.session(FauxClient(deco, voies=R.VOIES_CARTE[:7]), 1.0, tag + "nc", deco)
    code_nc = asyncio.run(non_conforme())
    assert code_nc == 4, code_nc

    # le diffuseur seul refuse BRUYAMMENT des voies qui ne correspondent pas au flux (push_chunk aplatirait)
    from ubri_connect import chaine
    r, w = os.pipe()
    os.write(w, T.encoder_ouverture(R.VOIES_CARTE[:7], DEPART, 250, 24) + T.encoder(DEPART, [0] * 7, False, None, (0, 0)))
    os.close(w)
    try:
        chaine.diffuseur(r, tag + "d", 250.0, time.monotonic() + 5, canaux=R.VOIES_CARTE)
        raise AssertionError("diffuseur : 7 voies publiées sur un flux à 8 sans erreur")
    except ValueError as e:
        assert "ouverture à 7 voies" in str(e), e
    print("OK  LSL réel : ouverture à 7 voies => code 4 ; diffuseur seul => ValueError nommée")


if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_"):
            f()
