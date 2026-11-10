# Application Zephyr de la carte UBRI v0 (nRF52840)

**Compilée à blanc le 2026-11-09 (Zephyr v4.4.2, SDK 1.0.1, carte `nrf52840dk/nrf52840`), JAMAIS exécutée sur une
carte.** Tout ce qui touche le matériel est à valider au banc d'évaluation
([[achats-banc-eval]] : `vault/01-projects/ubri/hardware/achats-banc-eval.md`).

## Compiler
```bash
bash products/ubri/firmware/installer_zephyr.sh        # une fois : empreintes du SDK FIGÉES dans le script
source ~/zephyrproject/.venv/bin/activate && export ZEPHYR_BASE=~/zephyrproject/zephyr
west build -p always -b nrf52840dk/nrf52840 -d <dossier HORS du dépôt> products/ubri/firmware/app
```
Mesuré le 2026-11-09 : 193 Ko de code + 2 Ko de données en flash (sur 1 Mo), ≈ 45 Ko de RAM (sur 256 Ko), aucun
avertissement avec `-Wall -Wextra -Werror` sur le code UBRI.

## Sécurité de la liaison (l'EEG est une donnée de santé)
- Appairage **LE Secure Connections seulement**, authentifié : la pile tire une clé **ALÉATOIRE** à chaque appairage,
  la carte l'« affiche » sur sa console, l'hôte la saisit. Une clé figée se retrouve en observant un seul appairage :
  il n'y en a donc pas.
- **Fenêtre d'appairage** : bouton 1 du DK maintenu au démarrage, 60 s. Hors fenêtre, tout appairage est refusé
  (`pairing_accept`) ; seuls les hôtes **déjà liés** (liaison mémorisée en flash, `BT_SETTINGS`) se reconnectent.
- **Ouvrir la fenêtre EFFACE toutes les liaisons** (`bt_unpair`, après le chargement de la flash) : c'est le moyen de
  révoquer un hôte perdu ; chaque hôte se ré-appaire ensuite. Sans cela, `BT_MAX_PAIRED` = 2 atteint, tout nouvel
  appairage échouait. `bt_unpair` rend 0 sans condition (KE#294) : la carte RECOMPTE les liaisons restantes et
  referme la fenêtre s'il en reste, ou si la flash n'a pas pu être lue. Que l'effacement ait atteint la flash ne se
  prouve qu'au banc (essai 6).
- **Place unique** (`BT_MAX_CONN` = 1) : une connexion qui n'atteint pas L4 en **30 s** est coupée, sinon un appareil
  inconnu à portée la garderait et l'hôte lié ne pourrait plus se connecter. Il peut se reconnecter et recommencer :
  la garde limite le blocage, elle ne l'empêche pas (pas de liste d'adresses refusées).
- Niveau **L4** exigé dès la connexion ; échec ou niveau inférieur => déconnexion ; l'annonce repart ensuite (KE#292).
- Commande et abonnement au flux en permissions **AUTHEN** ; le fil BLE relit en plus le niveau de la liaison avant
  chaque envoi (deux barrières indépendantes).

## SÉCURITÉ DE LA PERSONNE : jamais de câble USB sur le DK pendant qu'une personne porte les électrodes (KE#285)
La console du DK (journal, clé d'appairage) sort par la sonde J-Link, donc par l'USB du PC : brancher ce câble
relie la masse des électrodes au PC et au secteur, même si le DK est sur piles. L'**appairage se fait SANS électrode
posée**. Les compteurs de session (fronts incertains, lectures jetées, refus) arrivent à l'hôte dans la clôture v2
(0x83) : la console n'est plus nécessaire pour les lire.

## Ce que fait l'application
| Fichier | Rôle |
|---|---|
| `../coeur/ubri_trame.c` | le cœur testé sur PC (30 tests, parité avec `trame.py`), compilé TEL QUEL |
| `src/compteur_drdy.c` | DRDY (front descendant) -> GPIOTE IN -> PPI -> TIMER2 COUNT (compte matériel, indépendant de l'interruption) ; la même broche réveille le fil d'acquisition. Montage repris de l'exemple Zephyr `uac2_explicit_feedback` (nrfx 4.2) |
| `src/ads1299.c` | SPI mode 1 à 2 MHz ; `ads1299_configurer` : RESET, SDATAC, ID (0x3E attendu), registres (250 SPS, gain 24, GPIO en entrée, SRB1, tampon BIAS), relecture de CONFIG1 ; `ads1299_lancer` : START, RDATAC (le TIMER est remis à zéro entre les deux) ; lecture des 27 octets |
| `src/main.c` | fil d'ACQUISITION (seul à appeler `ubri_*`) : DRDY, TIMER, lecture SPI, TIMER relu (différent => lecture jetée), trame, file, 2 s sans DRDY => session close ; fil BLE : trames entières d'une même session, dans l'ordre, notification ; pertes postées avec leur session (KE#291) ; service GATT UBRI (COMMANDE : 0x01 ouvrir, 0x02 fermer ; FLUX : notifications) ; appairage, annonce relancée, MTU demandée par la carte |
| `boards/nrf52840dk_nrf52840.overlay` | SPI de l'en-tête Arduino (spi3) ; DRDY P1.10, START P1.11, RESET P1.08 ; câblage vers J3 de la carte TI |
| `dts/bindings/ubri,ads1299.yaml` | liaison devicetree propre au projet |

## Non vérifié (à faire au banc, dans cet ordre, SANS personne branchée jusqu'à l'étape 9)
1. Brochage DK -> J3 de la carte TI contre le **schéma** du guide (pas la sérigraphie) ; DVDD et VDD du DK sur le
   même rail 3,0 V, sur piles (fiche d'achat A12, KE#285).
2. Lecture de l'ID (0x3E) et relecture de CONFIG1 ; capture d'un WREG à l'analyseur (espacement des octets à 2 MHz).
3. Valeurs de registres (fiche SBAS499C) : 250 SPS mesurés au compteur, gain 24, BIAS.
4. DRDY à 250 Hz à l'analyseur = compte de TIMER2 sur 60 s, en égalité exacte ; 5 DRDY masquées exprès = exactement
   5 sauts dans les trames ; lecture SPI ralentie exprès : le compteur saute et les « lectures jetées » montent.
5. Ordre des bits GPIO (synchro sur GPIO1 supposé) et polarité de la ligne de synchro.
6. Appairage dans la fenêtre (clé refusée si fausse, appairage refusé hors fenêtre), puis 3 reconnexions et un
   appairage raté suivi d'un bon, sans redémarrage ; après un redémarrage, l'hôte lié se reconnecte sans clé ;
   révocation : appairer A et B, redémarrer AVEC le bouton et n'appairer que B, redémarrer SANS bouton => A refusé,
   B reconnecté sans clé (seule preuve de l'effacement en flash) ; partition settings effacée + bouton => la console
   dit « fenêtre refermée ». Connexion d'un téléphone qui
   n'appaire pas : coupée à 30 s (chronométrer aussi un vrai appairage : la clé doit être saisie avant).
7. Session A avec pertes forcées, déconnexion, session B propre : la clôture de B annonce **exactement** 0 abandon.
7 bis. Commandes rapprochées. Deux écritures GATT sont séparées d'au moins un intervalle de connexion (7,5 ms, 15 ms
   sous macOS) : ADS en marche (4 ms), l'ancien code voyait déjà « fermer » ; le cas ne se produit que fil bloqué.
   (a) Fil DRDY débranché (attentes de 100 ms, comme l'essai 8) : « fermer » puis « ouvrir » d'affilée => clôture en
   moins de 100 ms après « fermer », puis ouverture avec un numéro neuf. Cassure obligatoire : retirer le test de seq
   de la boucle => la clôture n'arrive qu'au silence de 2 s (l'essai doit alors échouer).
   (b) N « ouvrir » pendant une session => N clôtures et N ouvertures, numéros tous distincts, jamais deux sessions
   mêlées. (c) Déconnexion juste après « ouvrir » : lu sur la CONSOLE de la carte (l'hôte déconnecté ne voit rien ;
   console USB : AUCUNE personne équipée, KE#285), une seule ligne « session close » avec 0 abandon, aucune session après.
8. Fil DRDY débranché en pleine session : clôture en moins de 2 s.
9. Débit et pertes réels sur 30 min à MTU 247 : pertes annoncées = pertes comptées par l'hôte (cahier §8).

## Limites connues
- Seules les trames d'ÉCHANTILLON perdues côté radio pendant la session sont annoncées (« abandonnées ») ;
  l'ouverture ou la clôture perdue ne l'est pas, et une perte après l'émission de la clôture non plus : l'hôte
  la voit comme perte « côté hôte » (format NRD v0.2) ou comme session non bornée.
- Une déconnexion ferme la session sans pouvoir envoyer la clôture : l'hôte voit une session non bornée
  (`perdus_total` = None, jamais 0).
- Clôture v2 (0x83, 34 octets) : fronts incertains, lectures jetées, refus (mot d'état corrompu, échec SPI) et
  incohérences du TIMER transmis à l'hôte. Invariant : jetées + refus <= DRDY manquées (chacun laisse une DRDY non
  servie) ; les incohérences du TIMER et les fronts incertains ne sont PAS des pertes (KE#300). Parité C/Python sur le
  scénario de 4 874 trames, avec les causes injectées. Mettre à jour `trame.py` sur le poste hôte AVANT de flasher :
  un hôte ancien jette 0x83 et voit une session non bornée.
- Lead-off : les bits LOFF_STAT ne valent rien tant que LOFF_SENSP/N = 0 (réglage actuel).
- Commandes : chaque écriture incrémente un compteur ; toute commande reçue pendant une session la ferme, et un
  « ouvrir » rouvre une session NEUVE (nouvelle ouverture, nouveau numéro). Un « ouvrir » en double redémarre donc la
  session : voulu (jamais deux sessions mêlées).
- Numéro de session (`gen`), course connue : le fil BLE compare la session d'un paquet perdu à la session courante,
  PUIS ajoute ses pertes ; si une nouvelle ouverture (numéro + 1, remise à zéro des pertes) tombe exactement entre ces
  deux instructions, les échantillons de CE paquet (au plus 5) sont imputés à la NOUVELLE session (KE#291). La session
  est relue juste avant chaque imputation (après l'envoi, qui peut attendre un tampon) : fenêtre de quelques
  instructions, et seulement si une perte radio coïncide avec une réouverture. Non provocable au banc ; pour la fermer
  tout à fait, poster la perte avec son numéro et laisser le fil d'acquisition comparer.
- Pas de liste d'appareils autorisés au-delà des liaisons mémorisées (`BT_MAX_PAIRED` = 2).
- Récepteur côté hôte : `products/ubri/connect/ubri_connect/recepteur_ble.py` (bleak), testé contre un faux client
  seulement (ce serveur n'a pas de radio).
