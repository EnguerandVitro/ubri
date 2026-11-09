"""Trame UBRI v0 : protocole carte -> hôte de la carte UBRI (D22). Conçu pour ce que le protocole Cyton n'a pas :
une somme de contrôle (aucun échantillon corrompu n'est livré sans le savoir), un compteur 32 bits (pas d'ambiguïté
après 256 paquets perdus), et l'état de l'entrée de synchronisation DANS chaque trame (DEV-20).

  [0:2]   0xA5 0x5A        synchro
  [2]     version (1)
  [3]     N voies (1..16, BORNÉ avant usage, KE#113)
  [4:8]   compteur d'échantillon, uint32 gros-boutiste
  [8:8+3N] N voies, int24 signé gros-boutiste (coups ADS1299)
  [8+3N]  état : bit 0 = entrée de synchronisation, bits 1..7 réservés (à 0)
  [9+3N:11+3N] CRC-16/CCITT (init 0xFFFF) sur les octets [2 : 9+3N], gros-boutiste
Longueur = 11 + 3N (35 octets pour 8 voies, contre 33 pour Cyton).

Version 2 (mesurée nécessaire au banc de bout en bout, voir connect.md) : 4 octets de plus après l'octet d'état,
« front » = compteur du DERNIER front montant de l'entrée de synchro (0xFFFFFFFF si aucun). Si la trame qui porte le
front est perdue, les trames suivantes le répètent : l'ancre de l'événement se reconstruit (le dejitter donne l'heure
de n'importe quel compteur). Longueur v2 = 15 + 3N ; CRC sur [2 : 13+3N].

Version 3 (cahier carte-ubri-v0, section 6) : v2 + 3 octets après le front : LOFF_STATP, LOFF_STATN (bits de lead-off
DC recopiés du mot d'état de l'ADS1299, un bit par voie) et un octet d'état de la carte (batterie, 0-255). Longueur
18 + 3N. Les bits de lead-off ne sont PAS des bits réservés : une électrode décollée se livre, elle ne se rejette pas.

Trames de session (même en-tête A5 5A, octet de type à la place de la version, N voies, CRC final) :
  OUVERTURE (0x81) : id carte uint32, microprogramme uint16, fs uint16 (Hz), gain (1 octet, parmi les gains ADS1299),
                     compteur de départ uint32, puis N codes de voie (index dans LIBELLES, distincts). Longueur 19 + N.
  CLÔTURE   (0x82) : dernier compteur émis uint32, DRDY manquées vues par la carte uint32, trames abandonnées par la
                     carte uint32. Longueur 18.
  CLÔTURE v2 (0x83) : les 3 champs de 0x82, puis 4 compteurs de diagnostic uint32 :
                     - fronts INCERTAINS : vus haut au 1er échantillon ou juste après un trou (instant inconnu, non
                       datés, KE#289) ; ne mesure AUCUNE perte (un front au 1er échantillon en donne un sans perte) ;
                     - lectures JETÉES (TIMER changé pendant la lecture SPI) et REFUS (mot d'état corrompu, échec SPI) :
                       chacun laisse UNE DRDY non servie, aussi comptée dans « DRDY manquées » : invariant vérifiable
                       jetées + refus <= DRDY manquées (ils expliquent des pertes, ils ne s'y ajoutent pas) ;
                     - INCOHÉRENCES du TIMER (figé, qui recule ou repart à zéro) : PAS des pertes, hors invariant,
                       peuvent dépasser les DRDY manquées (KE#300).
                     Longueur 34. Émise par le microprogramme ; 0x82 reste décodée (champs v2 à None).
Avec elles, les pertes de TÊTE (avant la première trame reçue) et de QUEUE (après la dernière) deviennent comptables
sans connaître la vérité du simulateur.
"""
import binascii
from dataclasses import dataclass, field

SYNC = b"\xa5\x5a"
VERSION = 1
VERSIONS = (1, 2, 3)
OUVERTURE, CLOTURE, CLOTURE2 = 0x81, 0x82, 0x83
CLOTURES = (CLOTURE, CLOTURE2)
TYPES = (*VERSIONS, OUVERTURE, *CLOTURES)
AUCUN_FRONT = 0xFFFFFFFF
N_MAX = 16
GAINS = (1, 2, 4, 6, 8, 12, 24)  # gains programmables de l'ADS1299 (fiche)
LIBELLES = ("Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4", "EOG", "Fp1", "F3", "F4", "P3", "P4", "O1", "O2", "Oz")


def longueur(n: int, version: int = 1) -> int:
    if version == OUVERTURE:
        return 19 + n
    if version == CLOTURE:
        return 18
    if version == CLOTURE2:
        return 34
    return {1: 11, 2: 15, 3: 18}[version] + 3 * n


def _int24(b: bytes, i: int) -> int:
    v = (b[i] << 16) | (b[i + 1] << 8) | b[i + 2]
    return v - (1 << 24) if v & 0x800000 else v


def _scelle(corps: bytearray) -> bytes:
    return SYNC + bytes(corps) + binascii.crc_hqx(bytes(corps), 0xFFFF).to_bytes(2, "big")


def encoder(compteur: int, voies: list[int], synchro: bool = False, front: int | None = None,
            loff: tuple[int, int] | None = None, etat_carte: int = 0) -> bytes:
    """front=None : trame v1 ; front = compteur du dernier front (ou AUCUN_FRONT) : trame v2 ;
    loff=(LOFF_STATP, LOFF_STATN) : trame v3 (front=None y vaut AUCUN_FRONT)."""
    n = len(voies)
    version = 3 if loff is not None else (1 if front is None else 2)
    if version == 3 and front is None:
        front = AUCUN_FRONT
    assert 1 <= n <= N_MAX and 0 <= compteur < 2**32 and (front is None or 0 <= front <= AUCUN_FRONT)
    assert loff is None or (0 <= loff[0] < 256 and 0 <= loff[1] < 256 and 0 <= etat_carte < 256)
    corps = bytearray([version, n]) + compteur.to_bytes(4, "big")
    for v in voies:
        assert -(1 << 23) <= v < (1 << 23), v
        corps += (v & 0xFFFFFF).to_bytes(3, "big")
    corps.append(1 if synchro else 0)
    if front is not None:
        corps += front.to_bytes(4, "big")
    if loff is not None:
        corps += bytes([loff[0], loff[1], etat_carte])
    return _scelle(corps)


def encoder_ouverture(voies: list[str], compteur_depart: int, fs: int, gain: int, id_carte: int = 0,
                      microprogramme: int = 0) -> bytes:
    codes = [LIBELLES.index(v) for v in voies]
    assert 1 <= len(codes) <= N_MAX and len(set(codes)) == len(codes) and gain in GAINS
    assert 0 <= compteur_depart < 2**32 and 0 < fs < 2**16 and 0 <= id_carte < 2**32 and 0 <= microprogramme < 2**16
    corps = (bytearray([OUVERTURE, len(codes)]) + id_carte.to_bytes(4, "big") + microprogramme.to_bytes(2, "big")
             + fs.to_bytes(2, "big") + bytes([gain]) + compteur_depart.to_bytes(4, "big") + bytes(codes))
    return _scelle(corps)


def encoder_cloture(n: int, dernier_emis: int, drdy_manquees: int, abandonnees: int) -> bytes:
    assert 1 <= n <= N_MAX and all(0 <= v < 2**32 for v in (dernier_emis, drdy_manquees, abandonnees))
    corps = (bytearray([CLOTURE, n]) + dernier_emis.to_bytes(4, "big") + drdy_manquees.to_bytes(4, "big")
             + abandonnees.to_bytes(4, "big"))
    return _scelle(corps)


def encoder_cloture2(n: int, dernier_emis: int, drdy_manquees: int, abandonnees: int, fronts_incertains: int,
                     lectures_jetees: int, refus: int, incoherences_timer: int) -> bytes:
    champs = (dernier_emis, drdy_manquees, abandonnees, fronts_incertains, lectures_jetees, refus, incoherences_timer)
    assert 1 <= n <= N_MAX and all(0 <= v < 2**32 for v in champs)
    corps = bytearray([CLOTURE2, n])
    for v in champs:
        corps += v.to_bytes(4, "big")
    return _scelle(corps)


@dataclass
class Trame:
    compteur: int
    voies: tuple[int, ...]
    synchro: bool
    front: int | None = None  # v2/v3 ; None = pas de front connu ou trame v1
    loff: tuple[int, int] | None = None  # v3 : (LOFF_STATP, LOFF_STATN)
    etat_carte: int | None = None  # v3


@dataclass
class Ouverture:
    voies: tuple[str, ...]
    compteur_depart: int
    fs: int
    gain: int
    id_carte: int
    microprogramme: int


@dataclass
class Cloture:
    dernier_emis: int
    drdy_manquees: int
    abandonnees: int
    fronts_incertains: int | None = None  # clôture v2 seulement ; None = non transmis (clôture 0x82), jamais 0
    lectures_jetees: int | None = None
    refus: int | None = None
    incoherences_timer: int | None = None


@dataclass
class Decodeur:
    tampon: bytearray = field(default_factory=bytearray)
    dernier: int | None = None
    perdus: int = 0
    rejets_crc: int = 0
    rejets_domaine: int = 0  # CRC juste, champ de session hors domaine (KE#113) : compté à part
    octets_ecartes: int = 0
    trames: int = 0
    ouverture: Ouverture | None = None
    cloture: Cloture | None = None
    perdus_tete: int = 0
    perdus_queue: int = 0
    incoherences: list[str] = field(default_factory=list)  # jamais corrigées en silence : nommées (20 premières)
    incoherences_total: int = 0  # toutes, même au-delà des 20 gardées (une carte qui rejoue une trame en boucle)
    INCOHERENCES_MAX = 20

    def _incoherence(self, msg: str) -> None:
        self.incoherences_total += 1
        if len(self.incoherences) < self.INCOHERENCES_MAX:
            self.incoherences.append(msg)

    @property
    def perdus_total(self) -> int | None:
        """Toutes les pertes : tête (vs ouverture), trous, queue (vs clôture). None si la session n'est pas bornée
        (ouverture ou clôture absente) : une tête ou une queue inconnue n'est jamais 0 (KE#105/#111)."""
        if self.ouverture is None or self.cloture is None:
            return None
        return self.perdus_tete + self.perdus + self.perdus_queue

    def _session(self, typ: int, b: bytes) -> bool:
        """Décode une trame de session déjà vérifiée par CRC ; False si un champ est hors domaine (KE#113)."""
        n = b[3]
        if typ == OUVERTURE:
            gain, codes = b[12], b[17:17 + n]
            if gain not in GAINS or any(c >= len(LIBELLES) for c in codes) or len(set(codes)) != n:
                return False
            o = Ouverture(tuple(LIBELLES[c] for c in codes), int.from_bytes(b[13:17], "big"),
                          int.from_bytes(b[10:12], "big"), gain, int.from_bytes(b[4:8], "big"),
                          int.from_bytes(b[8:10], "big"))
            if self.ouverture is not None or self.trames:
                self._incoherence("ouverture reçue après le début de session")
            else:
                self.ouverture = o
            return True
        champs = (4, 8, 12, 16, 20, 24, 28) if typ == CLOTURE2 else (4, 8, 12)
        c = Cloture(*(int.from_bytes(b[k:k + 4], "big") for k in champs))
        if self.cloture is not None:
            self._incoherence("deuxième clôture")
            return True
        self.cloture = c
        if self.ouverture is None:
            self._incoherence("clôture sans ouverture : perte de tête inconnue")
        if self.dernier is None:
            if self.ouverture is not None:
                self.perdus_queue = (c.dernier_emis - self.ouverture.compteur_depart + 1) % 2**32
        elif (c.dernier_emis - self.dernier) % 2**32 >= 2**31:
            self._incoherence(f"clôture {c.dernier_emis} antérieure à la dernière trame reçue {self.dernier}")
        else:
            self.perdus_queue = (c.dernier_emis - self.dernier) % 2**32
        return True

    def nourrir(self, donnees: bytes) -> list[Trame]:
        self.tampon += donnees
        out: list[Trame] = []
        while len(self.tampon) >= 4:
            if self.tampon[0:2] != SYNC or self.tampon[2] not in TYPES or not (1 <= self.tampon[3] <= N_MAX):
                del self.tampon[0]
                self.octets_ecartes += 1
                continue
            ver = self.tampon[2]
            L = longueur(self.tampon[3], ver)
            if len(self.tampon) < L:
                break
            b = bytes(self.tampon[:L])
            crc_ok = binascii.crc_hqx(b[2:L - 2], 0xFFFF) == int.from_bytes(b[L - 2:L], "big")
            if ver in (OUVERTURE, *CLOTURES):
                if not crc_ok:
                    del self.tampon[0]
                    self.rejets_crc += 1
                    continue
                if not self._session(ver, b):
                    del self.tampon[0]
                    self.rejets_domaine += 1
                    continue
                del self.tampon[:L]
                continue
            ie = 8 + 3 * b[3]  # octet d'état
            if not crc_ok or b[ie] & 0xFE:
                del self.tampon[0]  # CRC faux ou bits réservés non nuls : on ne livre rien, on resynchronise
                self.rejets_crc += 1
                continue
            del self.tampon[:L]
            n = b[3]
            front = loff = etat = None
            if ver >= 2:
                f = int.from_bytes(b[ie + 1:ie + 5], "big")
                front = None if f == AUCUN_FRONT else f
            if ver == 3:
                loff, etat = (b[ie + 5], b[ie + 6]), b[ie + 7]
            t = Trame(int.from_bytes(b[4:8], "big"), tuple(_int24(b, 8 + 3 * i) for i in range(n)), bool(b[ie] & 1),
                      front, loff, etat)
            if self.cloture is not None:
                self._incoherence(f"trame {t.compteur} reçue après la clôture")
            if self.ouverture is not None and n != len(self.ouverture.voies):
                self._incoherence(f"trame {t.compteur} : {n} voies, ouverture : {len(self.ouverture.voies)}")
            if self.dernier is not None and ((t.compteur - self.dernier) % 2**32 >= 2**31 or t.compteur == self.dernier):
                # doublon ou retour en arrière : nommé, ni livré ni compté (sinon 2**32 - 1 « pertes »), et la
                # référence reste la plus haute vue (sinon le trou suivant serait gonflé)
                self._incoherence(f"compteur {t.compteur} non croissant après {self.dernier}")
                continue
            if self.dernier is not None:
                self.perdus += (t.compteur - self.dernier - 1) % 2**32
            elif self.ouverture is not None:
                ecart = (t.compteur - self.ouverture.compteur_depart) % 2**32
                if ecart >= 2**31:
                    self._incoherence(f"première trame {t.compteur} avant le départ annoncé")
                else:
                    self.perdus_tete = ecart
            self.dernier = t.compteur
            self.trames += 1
            out.append(t)
        return out
