"""Tests du décodeur Cyton. Lancer depuis products/ubri/connect : python3 -B tests/test_cyton.py"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106
from ubri_connect.cyton import Decodeur, encoder, int24, uv_par_coup  # noqa: E402


def test_int24_bornes():
    # Témoin exact (KE#121) sur les valeurs limites du complément à deux 24 bits.
    assert int24(b"\x7f\xff\xff") == 8388607
    assert int24(b"\x80\x00\x00") == -8388608
    assert int24(b"\xff\xff\xff") == -1
    assert int24(b"\x00\x00\x01") == 1
    assert abs(uv_par_coup(24) - 0.0223517445) < 1e-9
    print("OK  entiers 24 bits signés, échelle µV")


def test_aller_retour():
    rng = random.Random(0)
    flux, attendu = b"", []
    for n in range(600):
        v = [rng.randrange(-(1 << 23), 1 << 23) for _ in range(8)]
        aux = bytes(rng.randrange(256) for _ in range(6))
        flux += encoder(n % 256, v, aux, 0xC0 + n % 7)
        attendu.append((n % 256, tuple(v), aux, 0xC0 + n % 7))
    d = Decodeur()
    out = []
    for i in range(0, len(flux), 17):  # arrivée en morceaux de taille quelconque
        out += d.nourrir(flux[i:i + 17])
    assert [(p.numero, p.voies, p.aux, p.pied) for p in out] == attendu
    assert d.perdus == 0 and d.octets_ecartes == 0 and d.paquets == 600
    print("OK  600 paquets, décodage exact, numéro qui repasse 255 -> 0 sans perte comptée")


def test_pertes_et_resynchro():
    v = [1, -2, 3, -4, 5, -6, 7, -8]
    flux = encoder(10, v) + encoder(11, v) + encoder(14, v)  # 12 et 13 perdus
    flux += b"\xa0\x01\x02garbage\xc0\x00"  # octets corrompus (dont un faux en-tête)
    flux += encoder(15, v)
    flux += encoder(16, v)[:-1] + b"\x55"  # pied invalide : paquet rejeté
    flux += encoder(17, v)
    d = Decodeur()
    out = d.nourrir(flux)
    assert [p.numero for p in out] == [10, 11, 14, 15, 17], [p.numero for p in out]
    assert d.perdus == 2 + 1, d.perdus  # 12, 13, puis 16 (rejeté) vu comme perdu
    assert d.octets_ecartes > 0
    print(f"OK  pertes comptées ({d.perdus}), resynchro après corruption ({d.octets_ecartes} octets écartés)")


def test_saut_aberrant():
    v = [0] * 8
    d = Decodeur()
    d.nourrir(encoder(10, v) + encoder(10, v) + encoder(11, v))  # numéro répété : pas 255 pertes
    assert d.perdus == 0 and d.aberrants == 1, (d.perdus, d.aberrants)
    print("OK  numéro répété compté comme aberrant, pas comme 255 pertes")


def test_cassure_signe():
    # Cassure (KE#76) : un décodeur qui oublie le complément à deux doit être vu par le témoin.
    import ubri_connect.cyton as c
    orig = c.int24
    c.int24 = lambda b: (b[0] << 16) | (b[1] << 8) | b[2]
    try:
        out = c.Decodeur().nourrir(encoder(0, [-1] * 8))
        assert out[0].voies != tuple([-1] * 8), "la cassure du signe n'est pas vue"
    finally:
        c.int24 = orig
    print("OK  cassure du signe détectée par l'aller-retour")


if __name__ == "__main__":
    test_int24_bornes()
    test_aller_retour()
    test_pertes_et_resynchro()
    test_saut_aberrant()
    test_cassure_signe()
    print("TOUT VERT")
