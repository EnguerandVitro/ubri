# Cœur du microprogramme de la carte UBRI v0

Logique pure, en C99 sans dépendance, du microprogramme du nRF52840 ([[carte-ubri-v0]] :
`vault/01-projects/ubri/hardware/carte-ubri-v0.md`). Le même `ubri_trame.c` sera compilé dans l'application Zephyr ;
seuls le pilote SPI, le TIMER piloté par DRDY et la radio restent à écrire autour.

| Fonction | Rôle |
|---|---|
| `ubri_ouvrir` | trame d'OUVERTURE (0x81) : compteur de départ, fs, gain, carte des voies, id carte, microprogramme |
| `ubri_echantillon` | lit les 27 octets de l'ADS1299 (mot d'état `1100` + lead-off P/N + GPIO, puis 8 voies int24), compteur = départ + TIMER − 1, DRDY manquées = sauts du TIMER, front de synchro = premier échantillon servi à niveau haut, trame v3 |
| `ubri_abandon` | trames que la radio n'a pas pu prendre : comptées, jamais oubliées |
| `ubri_timer_coherent` | appelé par le pilote AVANT la lecture SPI : un TIMER déjà servi (interruption en retard) compte une incohérence et la lecture n'a pas lieu |
| `ubri_lecture_jetee`, `ubri_refus_lecture` | le pilote signale une lecture jetée (TIMER changé pendant la lecture) ou un échec SPI |
| `ubri_cloturer` | trame de CLÔTURE v2 (0x83, 34 octets) : dernière valeur du TIMER, DRDY manquées (queue comprise), abandons, fronts incertains, lectures jetées, refus (mot d'état corrompu, échec SPI), incohérences du TIMER (figé, qui recule, qui repart). Invariant vérifié : jetées + refus <= DRDY manquées (chacun laisse une DRDY non servie). Les incohérences et les fronts incertains ne sont PAS des pertes, hors invariant (KE#300) |

Refus nommés (rien n'est écrit) : mot d'état sans en-tête `1100` (lecture SPI corrompue), TIMER qui ne progresse
pas, gain hors table, code de voie hors table ou en double, plus de 8 voies, appel hors session.

## Tests (sur PC, gcc)
```bash
make test     # 40 tests, avec -Wconversion -Werror et les sanitizers d'adresse et de comportement indéfini
make parite   # parité C <-> Python sur 5 000 DRDY + 11 mutations du source, chacune rouge sur son motif
```
- **Vecteurs figés** : 4 trames (ouverture, v3 aux bornes int24 avec un front légitime, clôture v2, clôture v2 d'une
  session vide) calculées par `trame.py` et recopiées dans `tests/test_coeur.c` ; le C les reproduit à l'octet
  (KE#40). Parseur du mot d'état testé sur des octets LITTÉRAUX (C8 12 41), pas par son inverse.
- **Front de synchro (KE#289)** : daté seulement si l'échantillon précédent a été servi et lu bas ; au premier
  échantillon ou après un trou, il est compté « incertain » (`fronts_incertains`, transmis dans la
  clôture v2) ; côté hôte, l'événement apparaît comme « ancre perdue ».
- **Parité sur scénario** (`tests/parite.py`) : les attendus viennent de la VÉRITÉ du scénario (somme des DRDY
  ratées, débuts d'impulsion servis), jamais d'une recopie du C. Contrôles : pertes vues par l'hôte = annoncées par la
  clôture ; clôture = vérité ; fronts incertains, lectures jetées, refus et incohérences du TIMER REÇUS par l'hôte
  (clôture v2) = vérité du scénario, qui injecte des lectures jetées (J), des échecs SPI (R), des mots d'état
  corrompus (X) et des interruptions en retard (Z, même TIMER) ; invariant jetées + refus <= DRDY manquées ; puis les
  octets. Couverture forcée et vérifiée : 3 impulsions au début raté, 2 à la DRDY précédente ratée, 1 entièrement
  ratée ; J, R et X en tête, au milieu et en queue ; Z au premier et au dernier échantillon. Mesuré le 2026-11-09 :
  4 874 trames identiques à l'octet, 237 pertes (128 DRDY + 109 abandons) vues = annoncées = vérité, 5 fronts
  incertains, 16 lectures jetées, 27 refus et 94 incohérences du TIMER = vérité.
- **Mutations** (chacune rouge sur son contrôle) : compteur décalé de 1, front daté après un trou, incertains non
  comptés, DRDY de queue oubliées, abandons oubliés, mot d'état corrompu non compté, lectures jetées oubliées,
  compteurs v2 non transmis, incohérence du TIMER comptée en refus, CRC initialisé à 0, lead-off P et N inversés.
- **Contrat** (en-tête) : un seul contexte d'appel par session ; synchro active haute ; ordre des bits GPIO et
  validité du lead-off à confirmer au banc ; TIMER lu avant et après la lecture SPI.

## Reste à faire
- Application Zephyr nRF52840 : **écrite et compilée à blanc** (`../app/`, voir son README) ; à valider au banc.
- Limite : le compteur 32 bits repasse par 0 après 2³² DRDY (≈ 198 jours à 250 Hz) ; le TIMER qui « recule » est
  alors refusé (`UBRI_ERR_COMPTEUR`). Hors sujet pour des sessions de 30 min.
