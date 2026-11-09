"""Tests de la trame UBRI v0. Lancer : python3 -B tests/test_trame.py"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KE#106
from ubri_connect import trame as T  # noqa: E402


def test_aller_retour():
    rng = random.Random(1)
    flux, attendu = b"", []
    for c in range(2**32 - 300, 2**32) :  # passe aussi près de la borne du compteur
        v = [rng.randrange(-(1 << 23), 1 << 23) for _ in range(7)]
        s = rng.random() < 0.1
        flux += T.encoder(c, v, s)
        attendu.append((c, tuple(v), s))
    d = T.Decodeur()
    out = []
    for i in range(0, len(flux), 13):
        out += d.nourrir(flux[i:i + 13])
    assert [(t.compteur, t.voies, t.synchro) for t in out] == attendu
    assert d.perdus == 0 and d.rejets_crc == 0 and d.octets_ecartes == 0
    assert len(T.encoder(0, [0] * 8)) == 35
    print("OK  300 trames 7 voies, aller-retour exact, synchro portée, 35 octets pour 8 voies")


def test_chaque_bit_retourne_est_vu():
    # Témoin exhaustif (KE#118 : énuméré, pas échantillonné) : chaque bit de la trame retourné, un par un.
    base = T.encoder(123456, [100, -200, 300, -400, 500, -600, 700, -800], True)
    livres_faux = 0
    for i in range(len(base) * 8):
        b = bytearray(base); b[i // 8] ^= 1 << (i % 8)
        for t in T.Decodeur().nourrir(bytes(b)):
            if (t.compteur, t.voies, t.synchro) != (123456, (100, -200, 300, -400, 500, -600, 700, -800), True):
                livres_faux += 1
    assert livres_faux == 0, livres_faux
    print(f"OK  {len(base) * 8} retournements d'un bit : 0 trame fausse livrée")


def test_v2_front():
    base = T.encoder(1000, [1, -2, 3, -4, 5, -6, 7, -8], False, 990)
    assert len(base) == 39
    out = T.Decodeur().nourrir(base + T.encoder(1001, [0] * 8, False, T.AUCUN_FRONT) + T.encoder(1002, [0] * 8))
    assert [(t.compteur, t.front) for t in out] == [(1000, 990), (1001, None), (1002, None)]
    faux = 0
    for i in range(len(base) * 8):  # exhaustif aussi en v2 (KE#118)
        b = bytearray(base); b[i // 8] ^= 1 << (i % 8)
        for t in T.Decodeur().nourrir(bytes(b)):
            if (t.compteur, t.voies, t.synchro, t.front) != (1000, (1, -2, 3, -4, 5, -6, 7, -8), False, 990):
                faux += 1
    assert faux == 0, faux
    print(f"OK  trame v2 (front) : aller-retour, v1 et v2 mêlées, {len(base) * 8} retournements d'un bit -> 0 faux")


def test_borne_n():
    b = bytearray(T.encoder(1, [1] * 8)); b[3] = 200  # N hors domaine : jamais utilisé comme longueur
    d = T.Decodeur(); assert d.nourrir(bytes(b)) == [] and d.octets_ecartes > 0
    print("OK  N voies hors domaine refusé avant usage (KE#113)")


def test_cassure_crc():
    orig = T.binascii.crc_hqx
    T.binascii.crc_hqx = lambda data, init: 0  # CRC neutralisé : le témoin exhaustif doit rougir
    try:
        base = T.encoder(5, [1, 2, 3, 4, 5, 6, 7, 8])
        b = bytearray(base); b[10] ^= 0x10
        out = T.Decodeur().nourrir(bytes(b))
        assert out and out[0].voies != (1, 2, 3, 4, 5, 6, 7, 8), "cassure CRC non vue"
    finally:
        T.binascii.crc_hqx = orig
    print("OK  cassure : sans CRC, une trame corrompue est livrée (le témoin la voit)")


V3 = dict(compteur=2000, voies=[1, -2, 3, -4, 5, -6, 7, -8], synchro=True, front=1990, loff=(0b10000001, 0b00000100),
          etat_carte=200)


def _cle(t):
    return (t.compteur, t.voies, t.synchro, t.front, t.loff, t.etat_carte)


def test_v3_aller_retour_et_bits():
    base = T.encoder(**V3)
    assert len(base) == 42 == T.longueur(8, 3)  # cahier carte-ubri-v0 : 15 + 3 + 3 × 8
    attendu = (2000, (1, -2, 3, -4, 5, -6, 7, -8), True, 1990, (0b10000001, 0b00000100), 200)
    out = T.Decodeur().nourrir(base + T.encoder(2001, [0] * 8, False, None, (0, 0), 0) + T.encoder(2002, [0] * 8, False, 5))
    assert [_cle(t) for t in out] == [attendu, (2001, (0,) * 8, False, None, (0, 0), 0), (2002, (0,) * 8, False, 5, None, None)]
    faux = 0
    for i in range(len(base) * 8):  # exhaustif (KE#118)
        b = bytearray(base); b[i // 8] ^= 1 << (i % 8)
        faux += sum(_cle(t) != attendu for t in T.Decodeur().nourrir(bytes(b)))
    assert faux == 0, faux
    print(f"OK  trame v3 : 42 octets, v1/v2/v3 mêlées, {len(base) * 8} retournements d'un bit -> 0 faux")


def test_v3_leadoff_livre():
    # Une électrode décollée est une INFORMATION à livrer, jamais une trame à rejeter (revue cahier P1-2).
    for lp, ln in ((0xFF, 0xFF), (0x01, 0x00), (0x00, 0x80)):
        d = T.Decodeur(); out = d.nourrir(T.encoder(7, [0] * 8, False, None, (lp, ln), 255))
        assert len(out) == 1 and out[0].loff == (lp, ln) and d.rejets_crc == 0, (lp, ln)
    print("OK  lead-off non nul : trame livrée avec ses bits, jamais rejetée")


def _session(depart, emis, recus, drdy=0, aband=0):
    """Flux : ouverture, trames des compteurs `recus` (sous-ensemble des émis), clôture."""
    f = T.encoder_ouverture(["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4", "EOG"], depart, 250, 24, 0xC0FFEE, 3)
    f += b"".join(T.encoder(c, [c % 100] * 8, False, None, (0, 0), 0) for c in recus)
    return f + T.encoder_cloture(8, emis[-1], drdy, aband)


def test_session_pertes_tete_queue():
    emis = list(range(1000, 1100))
    recus = [c for c in emis if not (c < 1007 or 1040 <= c < 1043 or c >= 1095)]  # 7 en tête, 3 au milieu, 5 en queue
    d = T.Decodeur(); out = d.nourrir(_session(1000, emis, recus))
    assert [t.compteur for t in out] == recus
    assert (d.perdus_tete, d.perdus, d.perdus_queue, d.perdus_total) == (7, 3, 5, 15), (d.perdus_tete, d.perdus,
                                                                                         d.perdus_queue)
    o = d.ouverture
    assert (o.voies[-1], o.compteur_depart, o.fs, o.gain, o.id_carte, o.microprogramme) == ("EOG", 1000, 250, 24,
                                                                                          0xC0FFEE, 3)
    assert d.cloture == T.Cloture(1099, 0, 0) and d.incoherences == []
    d = T.Decodeur(); d.nourrir(_session(1000, emis, []))  # rien reçu : toute la session est en queue
    assert d.perdus_total == 100
    # incohérences nommées, jamais corrigées en silence
    d = T.Decodeur(); d.nourrir(_session(1000, [1050], [1060]))
    assert d.incoherences and "antérieure" in d.incoherences[0]
    d = T.Decodeur(); d.nourrir(_session(1000, emis, [990]))
    assert d.incoherences and "avant le départ" in d.incoherences[0]
    print("OK  session : pertes tête 7 / milieu 3 / queue 5 comptées sans vérité ; incohérences nommées")


def test_session_bits_et_domaines():
    flux = _session(1000, [1000], [1000])
    L_o = T.longueur(8, T.OUVERTURE); L_c = T.longueur(8, T.CLOTURE)
    assert (L_o, L_c) == (27, 18)
    ref = T.Decodeur(); ref.nourrir(flux)
    faux = 0
    for zone in (range(0, L_o), range(len(flux) - L_c, len(flux))):  # chaque bit des deux trames de session
        for o in zone:
            for k in range(8):
                b = bytearray(flux); b[o] ^= 1 << k
                d = T.Decodeur(); d.nourrir(bytes(b))
                if (d.ouverture not in (None, ref.ouverture)) or (d.cloture not in (None, ref.cloture)):
                    faux += 1
    assert faux == 0, f"session : faux={faux}"
    # domaines : gain et codes de voie hors table refusés APRÈS un CRC valide (KE#113)
    for off, val in ((12, 3), (17, 200), (18, 0)):  # gain 3 inexistant ; code 200 ; code dupliqué (Fz deux fois)
        b = bytearray(T.encoder_ouverture(["Fz", "FCz", "Cz"], 0, 250, 24)); b[off] = val
        b[-2:] = T.binascii.crc_hqx(bytes(b[2:-2]), 0xFFFF).to_bytes(2, "big")  # CRC recalculé : seul le domaine juge
        d = T.Decodeur(); d.nourrir(bytes(b))
        assert d.ouverture is None and d.rejets_domaine == 1 and d.rejets_crc == 0, (off, val)
    print(f"OK  session : {(L_o + L_c) * 8} retournements d'un bit -> 0 faux ; gain, code de voie, doublon refusés")


def test_session_incoherences():
    O = T.encoder_ouverture(["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4", "EOG"], 1000, 250, 24)
    f = lambda c, n=8: T.encoder(c, [0] * n, False, None, (0, 0), 0)
    C = lambda last: T.encoder_cloture(8, last, 0, 0)
    cas = [  # (flux, motif attendu dans la première incohérence, perdus_total attendu)
        (f(1000) + O + f(1001) + C(1001), "ouverture reçue après le début", None),
        (O + f(1000) + C(1000) + C(1000), "deuxième clôture", 0),
        (O + f(1000) + C(1000) + f(1001), "reçue après la clôture", 0),
        (f(1005) + C(1009), "clôture sans ouverture", None),
        (O + f(1000) + f(1001, 3) + C(1001), "3 voies, ouverture : 8", 0),
        (O + f(1000) + f(1001) + f(1001) + f(1002) + C(1002), "non croissant après 1001", 0),
        (O + f(1000) + f(1003) + f(1001) + f(1004) + C(1004), "non croissant après 1003", 2),
    ]
    for flux, motif, total in cas:
        d = T.Decodeur(); d.nourrir(flux)
        assert d.incoherences and motif in d.incoherences[0], (motif, d.incoherences)
        assert d.perdus_total == total, (motif, d.perdus_total)
    d = T.Decodeur(); d.nourrir(O + f(1000) + f(1005))  # pas de clôture : la queue est inconnue, jamais 0
    assert d.perdus_total is None and d.perdus == 4
    d = T.Decodeur(); d.nourrir(O + f(1000) + f(1000) * 5000 + C(1000))  # carte qui rejoue une trame en boucle
    assert len(d.incoherences) == 20 and d.incoherences_total == 5000 and d.perdus_total == 0, \
        f"liste={len(d.incoherences)} total={d.incoherences_total} perdus={d.perdus_total}"
    print(f"OK  incohérences : {len(cas)} cas nommés au mot près ; session non bornée -> perdus_total None")


def _avec_module_casse(remplacer: str, par: str, test) -> str:
    """Charge une copie MUTÉE de trame.py (texte remplacé, exactement une occurrence), lance le test dessus avec
    le module mutant à la place de T. Renvoie le message d'échec ; lève si le test reste vert (cassure non vue)."""
    global T
    import types
    src = Path(T.__file__).read_text()
    assert src.count(remplacer) == 1, remplacer
    m = types.ModuleType("trame_casse"); exec(compile(src.replace(remplacer, par), "trame_casse", "exec"), m.__dict__)
    vrai, T = T, m
    try:
        test()
    except Exception as e:  # une jambe retirée peut aussi faire PLANTER (IndexError) : c'est rouge, et on le nomme
        return f"{type(e).__name__}: {e}"
    finally:
        T = vrai
    raise AssertionError(f"cassure NON vue : {remplacer!r} -> {par!r}")


def test_cloture_v2():
    """Clôture v2 (0x83) : 3 compteurs de diagnostic en plus ; la v1 (0x82) les laisse à None, JAMAIS 0 (KE#105)."""
    o = T.encoder_ouverture(["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4", "EOG"], 1000, 250, 24)
    tr = b"".join(T.encoder(c, [1] * 8, False, None, (0, 0), 0) for c in (1000, 1001, 1004))
    c2 = T.encoder_cloture2(8, 1006, 4, 0, 2, 1, 3, 7)
    assert T.longueur(8, T.CLOTURE2) == len(c2) == 34
    d = T.Decodeur(); out = d.nourrir(o + tr + c2)
    assert len(out) == 3 and d.incoherences == []
    assert d.cloture == T.Cloture(1006, 4, 0, 2, 1, 3, 7), d.cloture
    assert (d.perdus, d.perdus_queue, d.perdus_total) == (2, 2, 4)  # 1002-1003 puis 1005-1006 : 4 = annoncées
    d1 = T.Decodeur(); d1.nourrir(o + tr + T.encoder_cloture(8, 1006, 4, 0))
    assert d1.cloture == T.Cloture(1006, 4, 0) and d1.cloture.fronts_incertains is None and d1.cloture.refus is None
    # chaque bit de la clôture v2 retourné : jamais une autre clôture livrée (CRC), exhaustif (KE#118)
    flux = o + tr + c2
    faux = 0
    for off in range(len(flux) - 34, len(flux)):
        for k in range(8):
            b = bytearray(flux); b[off] ^= 1 << k
            dd = T.Decodeur(); dd.nourrir(bytes(b))
            if dd.cloture not in (None, d.cloture):
                faux += 1
    assert faux == 0, f"clôture v2 : faux={faux}"
    print("OK  clôture v2 : aller-retour exact (7 incohérences TIMER > 4 DRDY manquées : hors invariant), v1 décodée "
          "avec None (jamais 0), 272 retournements -> 0 faux")


def test_cassures_session():
    # Chaque jambe a sa mutation du SOURCE ; le test visé doit rougir (KE#76/#144 : même outil que le banc).
    cas = [("self.perdus_tete = ecart", "pass", test_session_pertes_tete_queue, "tête non comptée", "(0, 3, 5)"),
           ("            self.perdus_queue = (c.dernier_emis - self.dernier) % 2**32", "            pass",
            test_session_pertes_tete_queue, "queue non comptée", "(7, 3, 0)"),
           ("if gain not in GAINS or any(c >= len(LIBELLES) for c in codes) or len(set(codes)) != n:", "if False:",
            test_session_bits_et_domaines, "domaine non contrôlé", "(12, 3)"),
           ("if gain not in GAINS or any(c >= len(LIBELLES) for c in codes) or len(set(codes)) != n:",
            "if gain not in GAINS or len(set(codes)) != n:", test_session_bits_et_domaines,
            "codes de voie hors table", "IndexError"),
           ("if not crc_ok or b[ie] & 0xFE:", "if not crc_ok or b[ie] & 0xFE or (ver == 3 and (b[ie + 5] or b[ie + 6])):",
            test_v3_leadoff_livre, "lead-off traité en bits réservés", "(255, 255)"),
           ("                if not crc_ok:\n                    del self.tampon[0]\n                    self.rejets_crc += 1\n"
            "                    continue\n                if not self._session(ver, b):",
            "                if not self._session(ver, b):", test_session_bits_et_domaines,
            "CRC des trames de session ignoré", "session : faux="),
           ("if self.ouverture is not None or self.trames:", "if False:", test_session_incoherences,
            "ouverture tardive acceptée", "ouverture reçue après le début"),
           ("            self._incoherence(\"deuxième clôture\")\n", "", test_session_incoherences,
            "deuxième clôture muette", "deuxième clôture"),
           ("            self._incoherence(\"clôture sans ouverture : perte de tête inconnue\")\n",
            "            pass\n",
            test_session_incoherences, "clôture sans ouverture muette", "clôture sans ouverture"),
           ("if self.ouverture is not None and n != len(self.ouverture.voies):", "if False:",
            test_session_incoherences, "nombre de voies non contrôlé", "3 voies, ouverture : 8"),
           ("                continue\n            if self.dernier is not None:\n",
            "            if self.dernier is not None:\n", test_session_incoherences, "doublon compté en pertes",
            "4294967295"),
           ("        if len(self.incoherences) < self.INCOHERENCES_MAX:\n", "        if True:\n",
            test_session_incoherences, "incohérences non bornées", "liste=5000"),
           ("        self.incoherences_total += 1\n", "        pass\n", test_session_incoherences,
            "total des incohérences non compté", "total=0"),
           ('champs = (4, 8, 12, 16, 20, 24, 28) if typ == CLOTURE2 else (4, 8, 12)', 'champs = (4, 8, 12)',
            test_cloture_v2, "compteurs v2 ignorés", "Cloture(dernier_emis=1006, drdy_manquees=4, abandonnees=0, "
            "fronts_incertains=None"),
           ("            if ver in (OUVERTURE, *CLOTURES):", "            if ver in (OUVERTURE, CLOTURE):",
            test_cloture_v2, "clôture v2 traitée comme une trame d'échantillon", "AssertionError: None")]
    import contextlib, io
    for remplacer, par, test, nom, motif in cas:
        with contextlib.redirect_stdout(io.StringIO()):
            msg = _avec_module_casse(remplacer, par, test)
        assert motif in msg, f"cassure « {nom} » rouge pour une AUTRE raison (KE#139) : {msg[:120]}"
        print(f"OK  cassure « {nom} » : rouge")

if __name__ == "__main__":
    test_aller_retour(); test_chaque_bit_retourne_est_vu(); test_v2_front(); test_borne_n(); test_cassure_crc()
    test_v3_aller_retour_et_bits(); test_v3_leadoff_livre(); test_session_pertes_tete_queue()
    test_session_bits_et_domaines(); test_session_incoherences(); test_cloture_v2(); test_cassures_session()
    print("TOUT VERT")
