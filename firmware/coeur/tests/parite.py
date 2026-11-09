"""Parité C <-> Python du cœur du microprogramme. Lancer : make -C products/ubri/firmware/coeur parite

Les valeurs attendues viennent de la VÉRITÉ du scénario, jamais d'une recopie du calcul C (KE#40, KE#286) :
- DRDY ratées = somme du tableau `ratee` ; abandons = nombre de trames que la radio n'a pas prises ;
- fronts attendus = débuts d'impulsion dont la DRDY ET la précédente ont été servies (KE#289) ; les autres
  impulsions touchées par un trou donnent un front INCERTAIN (non daté), sauf si elles sont entièrement ratées.

Contrôles, dans cet ordre (chacun nomme sa différence) :
1. pertes vues par l'hôte (trame.Decodeur sur le flux réellement émis) = pertes ANNONCÉES par la clôture ;
2. clôture = vérité (DRDY ratées, abandons) ;
3. fronts incertains comptés par le C = vérité ; couverture forcée : ≥ 3 impulsions au début raté, ≥ 2 à la
   DRDY précédente ratée, ≥ 1 entièrement ratée (KE#111) ;
4. octets : chaque trame émise par le C = trame.py appliqué à la vérité.
Puis 7 mutations du source C : chacune doit rougir sur SON motif (KE#76, KE#139).
"""
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parents[2] / "connect"))  # KE#106 : relatif au fichier
from ubri_connect import trame as T  # noqa: E402

DEPART, BIT_SYNC, N_DRDY = 1000, 2, 5000
CANAUX = ["Fz", "FCz", "Cz", "CPz", "Pz", "C3", "C4", "EOG"]


def scenario(graine: int = 7):
    rng = random.Random(graine)
    haut = [False] * N_DRDY
    debuts = list(range(20, N_DRDY - 20, 97))  # impulsions de 2-3 échantillons (≥ 8 ms à 250 Hz, cahier §2)
    longueurs = {}
    for d in debuts:
        longueurs[d] = rng.randint(2, 3)
        for k in range(d, d + longueurs[d]):
            haut[k] = True
    ratee = [False] * N_DRDY
    for s in [0, 1, 2] + rng.sample(range(10, N_DRDY - 10), 40):  # tête + 40 rafales aléatoires
        for k in range(s, min(s + rng.randint(1, 4), N_DRDY)):
            ratee[k] = True
    for k in range(N_DRDY - 3, N_DRDY):  # queue : les 3 dernières DRDY jamais servies
        ratee[k] = True
    # couverture FORCÉE des cas de front touchés par un trou (sinon le hasard n'en produit aucun : 0/52 mesuré)
    for d in debuts[3:6]:
        ratee[d] = True                  # DRDY du début ratée, la suite de l'impulsion servie
    for d in debuts[8:10]:
        ratee[d - 1] = True              # DRDY précédant le début ratée, le début servi
    d = debuts[12]
    for k in range(d - 1, d + longueurs[d] + 1):
        ratee[k] = True                  # impulsion entièrement ratée (perte silencieuse côté front)

    servi = [not r for r in ratee]
    dates = {d for d in debuts if servi[d] and d > 0 and servi[d - 1]}
    incertains = sum(1 for d in debuts if d not in dates and any(servi[k] for k in range(d, d + longueurs[d])))
    couverture = {"début raté": sum(1 for d in debuts if not servi[d] and any(servi[k] for k in range(d, d + longueurs[d]))),
                  "DRDY précédente ratée": sum(1 for d in debuts if servi[d] and not servi[d - 1]),
                  "impulsion entièrement ratée": sum(1 for d in debuts if not any(servi[k] for k in range(d, d + longueurs[d])))}

    lignes, attendu, aband = [f"O {DEPART} {BIT_SYNC}"], [], 0
    jetees = refus = incoh = 0
    causes = {}  # (zone, cause) -> nombre : couverture FORCÉE puis vérifiée (KE#111)
    zone = lambda i: "tête" if i < 3 else ("queue" if i >= N_DRDY - 3 else "milieu")
    forcees = {0: "R", 1: "J", 2: "X", N_DRDY - 3: "J", N_DRDY - 2: "R", N_DRDY - 1: "X"}  # tête et queue ratées
    dernier_front = T.AUCUN_FRONT
    dernier_servi = None
    for i in range(N_DRDY):
        if ratee[i]:
            # Une DRDY non servie peut avoir une cause que le pilote connaît : lecture jetée (TIMER changé pendant la
            # lecture), échec SPI, ou mot d'état corrompu refusé par le cœur. Elle reste comptée en DRDY manquée.
            r = rng.random()
            cause = forcees.get(i) or ("J" if r < 0.15 else "R" if r < 0.25 else "X" if r < 0.35 else None)
            if cause == "J":
                lignes.append("J"); jetees += 1
            elif cause == "R":
                lignes.append("R"); refus += 1
            elif cause == "X":
                lignes.append(f"X {i + 1}"); refus += 1
            if cause:
                causes[(zone(i), cause)] = causes.get((zone(i), cause), 0) + 1
            continue
        v = [rng.randint(-(1 << 23), (1 << 23) - 1) for _ in range(8)]
        lp, ln, etat = rng.randrange(256), rng.randrange(256), rng.randrange(256)
        gpio = (rng.randrange(16) & ~(1 << BIT_SYNC)) | (int(haut[i]) << BIT_SYNC)
        lignes.append(f"E {i + 1} {etat} {lp} {ln} {gpio} " + " ".join(map(str, v)))
        compteur = DEPART + i
        if i in dates:
            dernier_front = compteur
        trame = T.encoder(compteur, v, i in dates, dernier_front, (lp, ln), etat)
        abandon = rng.random() < 0.02
        attendu.append((trame, abandon, compteur, tuple(v)))
        if abandon:
            lignes.append("A 1"); aband += 1
        # interruption en retard : le même TIMER revient, ERR_COMPTEUR, PAS une perte (KE#300) ; forcée au 1er et au
        # dernier échantillon servis, aléatoire ailleurs
        premier, dernier = dernier_servi is None, i == max(k for k in range(N_DRDY) if servi[k])
        if premier or dernier or rng.random() < 0.02:
            lignes.append(f"Z {i + 1}"); incoh += 1
            cle = "premier servi" if premier else "dernier servi" if dernier else "ailleurs"
            causes[(cle, "Z")] = causes.get((cle, "Z"), 0) + 1
        dernier_servi = i
    lignes.append(f"C {N_DRDY}")
    for z in ("tête", "milieu", "queue"):
        for c in ("J", "R", "X"):
            couverture[f"{c} en {z}"] = causes.get((z, c), 0)
    for cle in ("premier servi", "dernier servi", "ailleurs"):  # chacune comptée À PART (KE#111)
        couverture[f"Z au {cle}"] = causes.get((cle, "Z"), 0)
    verite = {"drdy": sum(ratee), "aband": aband, "incertains": incertains, "jetees": jetees, "refus": refus,
              "incoh": incoh, "couverture": couverture}
    cloture = T.encoder_cloture2(8, DEPART + N_DRDY - 1, verite["drdy"], aband, incertains, jetees, refus, incoh)
    ouverture = T.encoder_ouverture(CANAUX, DEPART, 250, 24, 0x0B1, 3)
    return "\n".join(lignes) + "\n", ouverture, attendu, cloture, verite


def executer(binaire: str, texte: str) -> list[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(texte)
    try:
        r = subprocess.run([binaire, f.name], capture_output=True, text=True, timeout=120)
    finally:
        Path(f.name).unlink()
    if r.returncode != 0:
        raise RuntimeError(f"binaire en échec ({r.returncode}) : {r.stderr[-300:]}")
    return r.stdout.split("\n")


def verifier(binaire: str) -> str | None:
    """None si tout est conforme, sinon la PREMIÈRE différence, nommée."""
    texte, ouverture, attendu, cloture, verite = scenario()
    sortie = [l for l in executer(binaire, texte) if l]
    refus_c = [l for l in sortie if l.startswith("ERR ")]
    lignes = [l for l in sortie if not l.startswith("ERR ")]
    if any(l not in ("ERR -2", "ERR -3") for l in refus_c):
        return f"refus inattendus du cœur : {sorted(set(refus_c))}"
    if not lignes or not lignes[-1].startswith("I "):
        return "sortie du binaire sans ligne « I » (fronts incertains)"
    incertains_c = int(lignes[-1].split()[1])
    trames = lignes[:-1]
    if len(trames) != len(attendu) + 2:
        return f"{len(trames)} trames émises, {len(attendu) + 2} attendues"
    # flux réellement émis : trames que la radio a abandonnées retirées (drapeaux du scénario)
    flux = bytes.fromhex(trames[0])
    for (_, abandon, _, _), s in zip(attendu, trames[1:-1]):
        if not abandon:
            flux += bytes.fromhex(s)
    flux += bytes.fromhex(trames[-1])
    d = T.Decodeur()
    d.nourrir(flux)
    if d.cloture is None or d.ouverture is None:
        return f"ouverture ou clôture non décodée (rejets CRC {d.rejets_crc}, domaine {d.rejets_domaine})"
    annonce = d.cloture.drdy_manquees + d.cloture.abandonnees  # ce que la CARTE dit avoir perdu
    if d.perdus_total != annonce:  # 1. hôte vs annonce (KE#286), pas vs le modèle
        return f"pertes vues par l'hôte {d.perdus_total} != annoncées par la clôture {annonce}"
    if (d.cloture.drdy_manquees, d.cloture.abandonnees) != (verite["drdy"], verite["aband"]):  # 2. annonce vs vérité
        return (f"clôture ({d.cloture.drdy_manquees}, {d.cloture.abandonnees}) != vérité "
                f"({verite['drdy']}, {verite['aband']})")
    if incertains_c != verite["incertains"]:  # 3. fronts non datés (état interne du cœur)
        return f"fronts incertains C {incertains_c} != vérité {verite['incertains']}"
    diag = (d.cloture.fronts_incertains, d.cloture.lectures_jetees, d.cloture.refus, d.cloture.incoherences_timer)
    if diag != (verite["incertains"], verite["jetees"], verite["refus"], verite["incoh"]):  # 3 bis. reçu par l'HÔTE
        return (f"clôture v2 (incertains, jetées, refus, incohérences) {diag} != vérité "
                f"({verite['incertains']}, {verite['jetees']}, {verite['refus']}, {verite['incoh']})")
    if d.cloture.lectures_jetees + d.cloture.refus > d.cloture.drdy_manquees:  # invariant documenté (KE#300)
        return "invariant jetées + refus <= DRDY manquées violé"
    if d.incoherences:
        return f"décodeur : incohérences {d.incoherences[:2]}"
    attendues = [ouverture.hex()] + [a[0].hex() for a in attendu] + [cloture.hex()]
    for k, (s, a) in enumerate(zip(trames, attendues)):  # 4. octets
        if s != a:
            return f"trame {k} différente : C {s[:40]}… / Python {a[:40]}…"
    return None


MUTATIONS = [  # (nom, texte du source, remplacement, motif attendu)
    ("compteur décalé de 1", "    uint32_t compteur = s->depart + timer - 1u;", "    uint32_t compteur = s->depart + timer;",
     "trame 1 différente"),
    ("front daté après un trou (ancienne règle)", "    int front = niveau && !s->sync_prec && !incertain;",
     "    int front = niveau && !s->sync_prec;", "trame 307 différente"),
    ("incertains non comptés", "    if (incertain) s->fronts_incertains++;", "    (void)incertain;", "fronts incertains C 0"),
    ("DRDY de queue oubliées", "    s->drdy_manquees += timer_final - s->timer_prec; /* DRDY de queue jamais servies */",
     "    /* oubli */", "pertes vues par l'hôte"),
    ("abandons oubliés", "    if (s && s->ouverte) s->abandonnees += n;", "    (void)s; (void)n;", "pertes vues par l'hôte"),
    ("mot d'état corrompu non compté", "    if ((ads[0] & 0xF0u) != 0xC0u) { s->refus++; return UBRI_ERR_ETAT_ADS; }",
     "    if ((ads[0] & 0xF0u) != 0xC0u) { return UBRI_ERR_ETAT_ADS; }", "clôture v2 (incertains, jetées, refus"),
    ("lectures jetées oubliées", "    if (s && s->ouverte) s->lectures_jetees++;", "    (void)s;",
     "clôture v2 (incertains, jetées, refus"),
    ("compteurs v2 non transmis", "    be32(p + 16, s->fronts_incertains);", "    be32(p + 16, 0u);",
     "clôture v2 (incertains, jetées, refus"),
    ("incohérence du TIMER comptée en refus (KE#300)", "{ s->incoherences_timer++; return UBRI_ERR_COMPTEUR; }",
     "{ s->refus++; return UBRI_ERR_COMPTEUR; }", "clôture v2 (incertains, jetées, refus"),
    ("CRC initialisé à 0", "    uint32_t crc = 0xFFFFu;", "    uint32_t crc = 0x0000u;", "non décodée"),
    ("lead-off P et N inversés", "    p[ie + 5] = loff_p; p[ie + 6] = loff_n;", "    p[ie + 5] = loff_n; p[ie + 6] = loff_p;",
     "trame 1 différente"),
]


def main() -> int:
    binaire = sys.argv[1]
    _, _, attendu, _, verite = scenario()
    seuils = {"début raté": 3, "DRDY précédente ratée": 2, "impulsion entièrement ratée": 1}
    manque = [k for k, v in verite["couverture"].items() if v < seuils.get(k, 1)]  # chaque cause, chaque zone : >= 1
    if manque:
        print(f"KO  couverture insuffisante : {verite['couverture']}")
        return 1
    err = verifier(binaire)
    if err:
        print(f"KO  parité : {err}")
        return 1
    print(f"OK  parité C <-> Python : {len(attendu) + 2} trames identiques à l'octet ; pertes vues par l'hôte = "
          f"annoncées = vérité ({verite['drdy']} DRDY + {verite['aband']} abandons) ; fronts incertains "
          f"{verite['incertains']} = vérité ; clôture v2 reçue par l'hôte : {verite['jetees']} jetées, "
          f"{verite['refus']} refus, {verite['incoh']} incohérences du TIMER = vérité ; couverture {verite['couverture']}")
    src = (ICI.parent / "ubri_trame.c").read_text()
    rouges = 0
    for nom, avant, apres, motif in MUTATIONS:
        assert src.count(avant) == 1, f"{nom} : texte à muter trouvé {src.count(avant)} fois"
        d = Path(tempfile.mkdtemp(prefix="coeur-mut-"))
        try:
            (d / "tests").mkdir()
            (d / "ubri_trame.c").write_text(src.replace(avant, apres))
            shutil.copy(ICI.parent / "ubri_trame.h", d / "ubri_trame.h")
            shutil.copy(ICI / "test_coeur.c", d / "tests" / "test_coeur.c")
            subprocess.run(["cc", "-std=c99", "-O2", "-o", str(d / "t"), str(d / "ubri_trame.c"),
                            str(d / "tests" / "test_coeur.c")], check=True, capture_output=True)
            e = verifier(str(d / "t"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if e is None:
            print(f"KO  cassure « {nom} » : la parité reste verte")
        elif motif not in e:
            print(f"KO  cassure « {nom} » : rouge mais pas sur « {motif} » : {e}")
        else:
            rouges += 1
            print(f"OK  cassure « {nom} » : {e[:100]}")
    print("TOUT VERT" if rouges == len(MUTATIONS) else f"CASSURES : {rouges}/{len(MUTATIONS)}")
    return 0 if rouges == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
