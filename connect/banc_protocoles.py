"""Banc comparatif Cyton vs trame UBRI v0 sous erreurs de bits aléatoires.
Lancer : python3 -B banc_protocoles.py [--n 20000]
Mesure, par taux d'erreur binaire (BER) injecté DANS LE FLUX D'OCTETS (simulation, pas la liaison réelle) :
- livrés justes : paquet livré IDENTIQUE à son original (voies + numéro/compteur + octets aux/état + pied) ;
- livrés FAUX : paquet livré qui diffère de son original en quoi que ce soit (corruption muette, le pire) ;
- jamais arrivés : n − paquets livrés, solde NET (un paquet fantôme né d'un faux en-tête est compté dans FAUX et
  masque une perte) ;
- pertes signalées : ce que le décodeur annonce avoir perdu.
Identification : les 8 voies portent des valeurs aléatoires 24 bits qui identifient l'échantillon d'origine ; un
paquet dont les voies ne correspondent à aucun original est FAUX (collision ~2^-192, négligeable).
"""
import argparse
import random

from ubri_connect import cyton, trame


def bruiter(flux: bytes, ber: float, rng: random.Random) -> bytes:
    b = bytearray(flux)
    nbits = len(b) * 8
    k = 0
    # tirages géométriques : position du prochain bit faux
    import math
    while True:
        k += int(math.log(1 - rng.random()) / math.log(1 - ber)) + 1 if ber > 0 else nbits + 1
        if k > nbits:
            break
        b[(k - 1) // 8] ^= 1 << ((k - 1) % 8)
    return bytes(b)


def mesurer(nom, enc, dec_cls, voies_de, ident_ok, n, ber, seed):
    rng = random.Random(seed)
    orig = [tuple(rng.randrange(-(1 << 23), 1 << 23) for _ in range(8)) for _ in range(n)]
    flux = b"".join(enc(i, list(v)) for i, v in enumerate(orig))
    bruite = bruiter(flux, ber, random.Random(seed + 1))
    d = dec_cls()
    out = []
    for i in range(0, len(bruite), 64):
        out += d.nourrir(bruite[i:i + 64])
    index = {v: i for i, v in enumerate(orig)}
    faux = sum(1 for p in out if voies_de(p) not in index or not ident_ok(p, index[voies_de(p)]))
    justes = len(out) - faux
    absents = max(0, n - len(out))
    fantomes = max(0, len(out) - n)
    # identité (justes + faux = livrés) : ce n'est PAS une vérification ; « absents » et « fantômes » sont des soldes
    # nets, un fantôme livré pendant qu'un vrai paquet manque se compense et reste invisible ici.
    return {"proto": nom, "ber": ber, "livrés justes": justes, "livrés FAUX": faux, "jamais livrés": absents,
            "fantômes": fantomes, "pertes signalées": d.perdus}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20000); a = ap.parse_args()
    print(f"{'protocole':10s} {'BER':>8s} {'justes':>8s} {'FAUX':>7s} {'absents':>8s} {'fantômes':>9s} {'signalés':>9s}  (sur {a.n})")
    for ber in (1e-6, 1e-5, 1e-4, 1e-3):
        for nom, enc, dec, vd, ok in (
            ("Cyton", lambda i, v: cyton.encoder(i % 256, v), cyton.Decodeur, lambda p: p.voies,
             lambda p, i: p.numero == i % 256 and p.aux == b"\x00" * 6 and p.pied == 0xC0),
            ("UBRI v0", lambda i, v: trame.encoder(i, v), trame.Decodeur, lambda t: t.voies,
             lambda t, i: t.compteur == i and t.synchro is False)):
            r = mesurer(nom, enc, dec, vd, ok, a.n, ber, 7)
            print(f"{r['proto']:10s} {ber:8.0e} {r['livrés justes']:8d} {r['livrés FAUX']:7d} {r['jamais livrés']:8d} "
                  f"{r['fantômes']:9d} {r['pertes signalées']:9d}")


if __name__ == "__main__":
    main()
