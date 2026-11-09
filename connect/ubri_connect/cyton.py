"""Décodeur du protocole série OpenBCI Cyton (carte de RÉFÉRENCE, D22), en Python pur, sans bibliothèque fabricant.

Paquet de 33 octets (documentation publique OpenBCI, à confirmer sur une vraie carte) :
  [0]      0xA0 (en-tête)
  [1]      numéro d'échantillon, 0..255, s'incrémente de 1 par paquet (repasse à 0 après 255)
  [2:26]   8 voies × 3 octets, entier signé 24 bits gros-boutiste (complément à deux), en « coups » de l'ADS1299
  [26:32]  6 octets auxiliaires (accéléromètre par défaut ; selon le mode de la carte, lectures de broches)
  [32]     pied 0xC0..0xC6 (le type de pied dit comment lire les octets auxiliaires ; la documentation décrit
           0xCX, X de 0 à F, les firmwares actuels n'émettent que C0-C6 : C7-CF sont refusés ici, à confirmer)
µV par coup = 4,5 V / gain / (2**23 - 1) × 1e6 ; gain 24 par défaut -> ≈ 0,02235 µV.

Le décodeur ne fait confiance à rien : il cherche l'en-tête, vérifie le pied, et sinon avance d'UN octet (resynchro).
Il compte les paquets perdus d'après les sauts du numéro d'échantillon (un saut de 1 = rien de perdu). Un saut de plus
de SAUT_MAX (numéro répété ou faux en-tête) n'est pas une perte plausible : compté à part dans `aberrants`.
"""
from dataclasses import dataclass, field

HEADER = 0xA0
FOOTERS = frozenset(range(0xC0, 0xC7))
PACKET = 33
SAUT_MAX = 32  # au-delà, un saut de numéro est jugé aberrant (faux en-tête, numéro corrompu), pas une perte
N_CH = 8
VREF = 4.5


def uv_par_coup(gain: int = 24) -> float:
    return VREF / gain / (2**23 - 1) * 1e6


def int24(b: bytes) -> int:
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - (1 << 24) if v & 0x800000 else v


@dataclass
class Paquet:
    numero: int
    voies: tuple[int, ...]  # 8 entiers signés 24 bits (coups)
    aux: bytes  # 6 octets bruts
    pied: int


@dataclass
class Decodeur:
    tampon: bytearray = field(default_factory=bytearray)
    dernier: int | None = None
    perdus: int = 0  # paquets manquants d'après le numéro d'échantillon (sauts plausibles seulement)
    aberrants: int = 0  # sauts de numéro > SAUT_MAX ou numéro répété
    octets_ecartes: int = 0  # octets jetés pendant une resynchro
    paquets: int = 0

    def nourrir(self, donnees: bytes) -> list[Paquet]:
        """Ajoute des octets reçus et rend les paquets complets et valides, dans l'ordre."""
        self.tampon += donnees
        sortie: list[Paquet] = []
        while len(self.tampon) >= PACKET:
            if self.tampon[0] != HEADER or self.tampon[PACKET - 1] not in FOOTERS:
                del self.tampon[0]  # resynchro octet par octet
                self.octets_ecartes += 1
                continue
            brut = bytes(self.tampon[:PACKET])
            del self.tampon[:PACKET]
            p = Paquet(brut[1], tuple(int24(brut[2 + 3 * i: 5 + 3 * i]) for i in range(N_CH)), brut[26:32], brut[32])
            if self.dernier is not None:
                saut = (p.numero - self.dernier - 1) % 256
                if saut <= SAUT_MAX:
                    self.perdus += saut
                else:
                    self.aberrants += 1
            self.dernier = p.numero
            self.paquets += 1
            sortie.append(p)
        return sortie


def encoder(numero: int, voies: list[int], aux: bytes = b"\x00" * 6, pied: int = 0xC0) -> bytes:
    """Inverse exact du décodeur (sert aux tests et à la carte simulée)."""
    assert len(voies) == N_CH and len(aux) == 6 and pied in FOOTERS and 0 <= numero <= 255
    out = bytearray([HEADER, numero])
    for v in voies:
        assert -(1 << 23) <= v < (1 << 23), v
        out += (v & 0xFFFFFF).to_bytes(3, "big")
    return bytes(out + aux + bytes([pied]))
