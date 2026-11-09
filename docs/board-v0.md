---
title: "UBRI : cahier de la carte d'acquisition UBRI v0 (D22)"
date: 2026-11-09
tags: [ubri, device, hardware, carte, ads1299, phase-0]
status: brouillon-v0.1
---

# Carte UBRI v0 : cahier avant schéma

La carte d'acquisition maison (décision D22) qui remplace le Cyton dans le device UBRI. Ce cahier traduit les
exigences [[exigences-device-v3]] et les bancs ([[banc-errp]], [[banc-lsl]], [[connect]]) en choix de conception.
Même convention d'origine que les exigences : **mesuré**, **simulé**, **calculé**, **plan**, **prudence**, plus
**fiche** (lu dans une documentation constructeur, **à revérifier sur la fiche technique à jour avant le schéma**).

## 1. Ce que la carte doit faire (rappel des exigences)

| Besoin | Valeur | Source |
|---|---|---|
| Voies | 7 EEG (Fz, FCz, Cz, CPz, Pz, C3, C4) + référence + masse active (BIAS) | DEV-01, **mesuré** (résultat 3) |
| 8e voie | **EOG** (électrode frontale ou sous l'œil) | DEV-13, **simulé** (résultat 8 : piège des clignements liés à l'erreur, raté sur 1 sujet sur 6 sans EOG) |
| Échantillonnage | 250 Hz (seule fréquence testée), 24 bits bruts | DEV-22, DEV-30 |
| Entrée de synchronisation | échantillonnée AVEC l'EEG, au même échantillon | DEV-20, **mesuré** (alignement 0,31 ms avec calibration) |
| Horloge | dérive ≤ 500 ppm (mesurée et déclarée) | DEV-23 |
| Qualité de contact | par électrode, pendant toute la session, + contrôle avant session | DEV-11, DEV-12 |
| Alimentation | batterie seule, jamais reliée au secteur pendant le port | DEV-52, **prudence** |
| Protocole | trame UBRI **v2**, étendue en **v3** pour le lead-off (CRC-16, compteur 32 bits, bit de synchro, dernier front répété) | [[connect]], **simulé** : v2 perd 0 ancre sur 59 à 20 % de pertes, v1 en perd 14 |
| Bruit propre | ≤ 0,5 µV efficaces par voie (section 8), très inférieur au bruit de contact de l'électrode sèche (le vrai goulot) | DEV-10, résultat 6 |

## 2. Convertisseur : ADS1299 (TI)

Choix conservé de D22, c'est le standard de l'EEG ouvert (OpenBCI Cyton, nombreuses cartes de recherche).
- 8 voies 24 bits simultanées, gain programmable jusqu'à 24, référence interne 4,5 V, amplificateur de BIAS
  (masse active) intégré, détection de décollement d'électrode (lead-off) en continu ou alternatif (**fiche**).
- **Bruit d'entrée : 0,98 µV crête-à-crête (0,14 µV efficaces) à 250 échantillons/s, au gain 24, bande 65 Hz**
  (**fiche**, tableau 4 ; 7,6 µV crête-à-crête au gain 1), à comparer à
  l'EEG de fond du banc (4,8 à 7,4 µV d'écart-type, résultat 8) : le convertisseur n'est pas le goulot, l'électrode
  l'est. À **mesurer** sur la carte : entrées court-circuitées (mode test interne du composant).
- **Entrée de synchronisation sur une broche GPIO de l'ADS1299** : le mot d'état de 24 bits qui précède chaque
  échantillon contient le niveau des 4 broches GPIO (**fiche** : « bits[4:7] of the GPIO register »). La synchro
  est alors lue par le convertisseur lui-même, dans le même mot que l'EEG, sans dépendre du logiciel du
  microcontrôleur. La fiche ne dit PAS à quel instant ce niveau est figé, ni le retard du filtre de décimation sur
  l'EEG : l'écart GPIO ↔ voie analogique est **à mesurer** (section 8). C'est un niveau lu une fois par
  échantillon : l'impulsion de synchro doit durer **≥ 2 échantillons (8 ms)** pour ne jamais être manquée **tant
  qu'aucune DRDY n'est ratée** ; après une DRDY ratée, le front n'est pas daté (KE#289) et la capture par minuterie
  est le contrôle croisé. Hypothèse à tenir : l'intervalle bas entre deux impulsions dépasse la plus longue rafale de
  DRDY ratées, sinon deux impulsions fusionnent et un front se perd sans être compté. En
  parallèle, une capture par minuterie du microcontrôleur date le front à la microseconde (contrôle croisé) ; le
  champ « front » de la trame v2 porte le COMPTEUR de l'échantillon du front, pas cette heure.
- Qualité de contact : lead-off alternatif ponctuel (7,8 ou 31,2 Hz, dans la bande, **fiche**) pour estimer
  l'impédance avant la session (DEV-12). Pendant la session (DEV-11) : excitation continue HORS bande à fDR/4
  (62,5 Hz à 250 Hz, retirée par le passe-bande 1-10 Hz, **fiche contradictoire** : registre FLEAD_OFF = 11 « fDR/4 » interne, mais
  le texte du §9.3.2.4.3.2 demande une source externe ; **à trancher au banc**), dont l'amplitude mesurée par voie donne la qualité écrite dans le flux. Les bits
  de lead-off DC du mot d'état ne signalent qu'une électrode DÉCOLLÉE, pas un mauvais contact encore branché (le cas
  mesuré de DEV-11) ; ils sont transportés en plus, dans une trame v3 (section 6) : la v2 n'a que 7 bits réservés,
  que le décodeur actuel exige à 0.
- Horloge : **oscillateur externe obligatoire**, 2,048 MHz (1,5-2,25 MHz admis, CLKSEL = 0, **fiche**), ≤ 50 ppm.
  L'oscillateur interne tient ±0,5 % à 25 °C et ±2,5 % sur la plage de température (**fiche**), soit 10 à 50 fois
  DEV-23 (500 ppm). 50 ppm laisse un facteur 10 de marge, et la dérive reste mesurée par le dejitter.
- Question ouverte : au gain 24, la pleine échelle d'entrée est d'environ ±187 mV (4,5 V / 24) ; la tension de
  décalage continue d'une électrode sèche pourrait saturer la voie. **À mesurer** avec DEV-10.

## 3. Microcontrôleur et liaison radio

**Débit nécessaire (calculé)** : trame v3 à 8 voies = 15 + 3 + 3 × 8 = **42 octets** (la v2 en fait 39), à 250 Hz
= **10,5 ko/s**, plus l'en-tête de transport. C'est faible : n'importe quelle liaison moderne suffit, le choix se fait sur la consommation,
l'isolation et la maturité logicielle.

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| **BLE (nRF52840)** | faible consommation ; radio = isolation galvanique ; pile BLE mûre (Zephyr / nRF Connect SDK) ; hôte Linux en Python pur (`bleak`, à essayer sur le poste d'acquisition : ce serveur EC2 n'a pas de radio Bluetooth) ; modules précertifiés | latence par intervalle de connexion (7,5 ms et plus) | **retenu** : la latence devrait être absorbée par la synchro matérielle et le dejitter sur compteur (**simulé** : latence choisie à 15-25 ms ; alignement 0,31 à 0,39 ms à 50 ppm, 0,59 ms à 500 ppm, [[banc-lsl]], [[connect]]). Latence et gigue BLE réelles **à mesurer** (section 8) |
| Wi-Fi (ESP32-S3) | débit, pas de dongle | consommation 5 à 10 fois plus forte (ordre de grandeur, **à vérifier sur fiche**), gigue Wi-Fi | écarté pour v0 |
| USB filaire | simple, sans perte | relie la personne au PC donc au secteur (DEV-52) sauf isolateur USB | **réservé au banc de développement**, avec isolateur |

- **Module nRF52840 précertifié** (radio déjà homologuée : pas de certification radio à refaire pour le prototype),
  modèle exact à choisir (**à vérifier** : certifications CE/FCC du module retenu).
- Liaison ADS1299 ↔ nRF52840 en SPI, interruption sur la broche DRDY (donnée prête). Le compteur d'échantillon
  32 bits est tenu par un TIMER matériel du nRF52840 en mode compteur, piloté par le front de DRDY (GPIOTE + PPI),
  indépendant de l'interruption ; la routine lit ce compteur à chaque trame. Une DRDY non servie fait sauter le
  compteur de la trame suivante : la perte est visible, jamais masquée (même principe que la trame v0). Un compteur
  incrémenté PAR l'interruption, lui, masquerait la perte (et la fiche ne décrit aucun indicateur de débordement).
- Langage du microprogramme : **C sous Zephyr** pour v0 (pile BLE la plus éprouvée sur nRF). Rust (embassy) reste
  une option si le banc montre un gain, conformément à D22 (« libres de repartir de zéro si les tests le montrent »).

## 4. Alimentation et isolation

- Batterie LiPo 1 cellule. **Charge impossible pendant le port, par construction mécanique** : le connecteur de
  charge n'est accessible que câble d'électrodes DÉBRANCHÉ (même logement), ou la batterie se retire du boîtier
  pour être chargée. Couper l'alimentation analogique ne suffit PAS : les électrodes restent reliées à la masse de
  la carte, donc à celle du chargeur, par les entrées et leurs diodes de protection. En plus, la carte refuse
  d'ouvrir une session pendant la charge. **Prudence**, DEV-52. Contrôle : chargeur branché, continuité de chaque
  broche d'électrode vers la masse USB = circuit ouvert.
- Partie analogique de l'ADS1299 en ±2,5 V (alimentation bipolaire, comme le Cyton : schéma OpenBCI, **à
  vérifier**) ou en 5 V unipolaire : choix à
  trancher **au banc de bruit** (section 8), pas sur le papier. Dans les deux cas : régulateurs linéaires à faible
  bruit après la conversion, plan de masse analogique séparé, conversion à découpage loin des entrées.
- Entrée de synchronisation **isolée par optocoupleur** quand elle vient d'un appareil relié au secteur (contrôleur
  du robot) ; directe pour une photodiode alimentée par la carte. **Prudence** : la seule liaison filaire vers
  l'extérieur ne doit pas rompre l'isolation de la personne.
- **Autonomie** : ADS1299 8 voies 39 mW typiques, 42 mW au plus (**fiche**, BIAS coupé ; + amplificateur de BIAS, à mesurer) + nRF52840 en flux BLE
  (≈ 10 mW, **à lire sur fiche**) ≈ 50 mW ; 300 mAh × 3,7 V ≈ 1,1 Wh ⇒ ≈ 20 h avant pertes des régulateurs
  (**calculé**), très au-delà de la session de 30 min (DEV-41). À remplacer par la mesure au banc.

## 5. Mécanique (rappel)
Électronique déportée hors de la tête, boîtier arrière ou ceinture (DEV-43) ; câbles d'électrodes blindés ou
électrodes actives à décider avec le choix de l'électrode sèche (DEV-42, mesure DEV-10 d'abord).

## 6. Protocole : trame v3 + trames de session

La trame de données part de la **v2** de [[connect]] (39 octets à 8 voies) et devient une **v3** (octet de
version 3) pour porter l'état de la carte. Trois ajouts, dont les deux trames de session ferment la limite
actuelle du banc (les pertes de tête et de queue de session y sont lues dans la vérité du simulateur, pas dans le
flux) :
- **Trame d'ouverture de session** (type distinct, CRC) : identifiant de carte, version du microprogramme, fs,
  gain, carte des voies (dont la voie EOG), facteur µV par coup, **compteur de départ**, réglage de l'horloge.
  Tout ce que `montage.json` et `info.json` du format NRD doivent déclarer, dicté par la carte elle-même.
- **Trame de clôture** : **dernière valeur du compteur TIMER** (dernière DRDY comptée, que sa trame ait été émise,
  abandonnée ou sa DRDY ratée : jamais le compteur de la dernière trame envoyée, sinon la queue est sous-comptée), nombre de DRDY manquées côté carte, nombre de trames abandonnées
  côté radio. L'hôte compare au nombre reçu : les pertes de tête et de queue deviennent comptables sans la vérité.
- **Trame v3** : v2 + 2 octets de lead-off (LOFF_STATP, LOFF_STATN recopiés du mot d'état) + 1 octet d'état de la
  carte (batterie), soit 15 + 3 + 3 × 8 = **42 octets**, 10,5 ko/s à 250 Hz (**calculé**). Le décodeur de
  [[connect]] doit apprendre la version 3 (`VERSIONS`, `longueur`) avant le premier essai, avec une cassure : bits
  de lead-off non nuls ⇒ trame livrée, jamais rejetée. Trame de clôture, v3 et compteur matériel de DRDY ont
  chacun leur scénario de cassure dans `banc_connect.py` AVANT le banc sur cartes d'évaluation : **fait**
  ([[connect]], section « Trame v3 et trames de session »).
- **À trancher au banc** : CRC-16 suffit-il ou faut-il CRC-32 (la liaison BLE a déjà son propre contrôle ; le CRC
  de la trame protège surtout la chaîne carte ↔ hôte de bout en bout), tests de pertes en rafale.

## 7. Feuille de route (dans cet ordre)
0. **Fait (2026-11-09)** : cœur logique du microprogramme en C portable (`products/ubri/firmware/coeur/`) ;
   parité d'encodage avec trame.py et attendus tirés de la vérité du scénario (4 874 trames, 7 mutations rouges).
   Front de synchro après un trou : non daté et compté (KE#289), transmis dans la clôture v2 (0x83, 34 octets :
   fronts incertains, lectures jetées, refus, incohérences du TIMER ; disposition dans `trame.py`, KE#300).
   **Application Zephyr écrite et compilée à blanc** (`products/ubri/firmware/app/`, Zephyr v4.4.2, 194 Ko de flash) :
   SPI ADS1299, TIMER2 compté par PPI depuis DRDY, service BLE chiffré et authentifié (LE Secure Connections, clé
   aléatoire, fenêtre d'appairage, liaisons mémorisées, niveau L4) ; jamais exécutée sur une carte. **Console du DK =
   USB du PC : jamais branchée pendant qu'une personne porte les électrodes** (KE#285).
1. **Banc sur cartes d'évaluation, avant tout circuit imprimé** : carte d'évaluation ADS1299 de TI + kit de
   développement nRF52840. Microprogramme v0 (SPI, DRDY sur compteur TIMER matériel, trame v3, BLE), hôte UBRI Connect en BLE.
   On mesure : bruit entrées court-circuitées, gigue et pertes BLE réelles, synchro par GPIO de l'ADS1299 contre
   photodiode. Le banc LSL et le banc Connect existants servent tels quels, la carte simulée est remplacée par la
   vraie.
2. **Schéma KiCad** (KiCad 7 et ses outils en ligne de commande sont sur ce serveur : contrôle électrique ERC et
   règles de dessin DRC automatisés à chaque étape).
3. **Circuit imprimé v0**, fabrication en petite série, puis comparaison avec le Cyton de **référence** sur la même
   tête et la même tâche (D22).
4. Banc d'acceptation des exigences (section 7 de [[exigences-device-v3]]), avec la voie EOG.

## 8. Mesures qui décident (aucun choix ci-dessus n'est définitif sans elles)

| Mesure | Décide | Seuil |
|---|---|---|
| Bruit entrées court-circuitées, 1-10 Hz, par voie, en µV efficaces (même grandeur que le fond) | alimentation bipolaire ou unipolaire, dessin des masses | ≤ 0,5 µV efficaces par voie (**plan** : ≈ 0,1 × le fond le plus faible, C3 4,8 µV ; fiche : 0,14 µV efficaces sur 65 Hz au gain 24) |
| Même impulsion photodiode envoyée sur la GPIO ET sur une voie analogique | entrée de synchro par GPIO ou par minuterie | écart GPIO ↔ voie analogique constant à ± 1 échantillon et déclaré ; puis alignement < 1 ms après calibration sur ≥ 40 impulsions (critère du banc LSL, DEV-21) |
| Pertes et gigue BLE réelles, 30 min, à 2 m et à travers le corps | intervalle de connexion, taille des tampons | 0 ancre perdue (trame v2/v3) sur ≥ 200 impulsions |
| Isolation pendant la charge | logement du connecteur de charge | chargeur branché : continuité électrode ↔ masse USB = circuit ouvert, sur chaque broche (DEV-52) |
| Dérive de l'oscillateur | oscillateur retenu | ≤ 500 ppm (DEV-23) |
| Autonomie en flux continu | capacité de la batterie | ≥ 2 h (plusieurs sessions) |
| Piège des clignements avec voie EOG : d'abord [[banc-errp]] avec une voie EOG simulée (6 sujets du jeu), puis enregistrement guidé réel (DEV-13) | garder l'EOG sur la 8e voie | banc : piège retiré sur 6 sujets sur 6, coût d'ErrP ≤ 0,01 (**plan**) ; réel : nombre de personnes à fixer |

## Questions ouvertes
- Électrodes actives (préamplificateur sur l'électrode) ou passives : dépend de la mesure DEV-10 sur électrode sèche.
- Modèle exact du module nRF52840 et de l'oscillateur : à choisir sur fiche et disponibilité.
- Achats du banc d'évaluation (étape 1) : liste [[achats-banc-eval]], à valider avant toute commande.
