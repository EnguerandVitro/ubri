---
title: "UBRI : banc ErrP sur BNCI 2015-013 (phase 0, S1-2)"
date: 2026-11-08
tags: [ubri, errp, banc, eeg, phase-0]
status: en-cours
---

# Banc ErrP : le signal d'erreur se détecte-t-il sur un inconnu ?

Code : `products/ubri/errp-bench/` (`prep.py`, `bench.py`, `splits.py`). Données dans `data/`, hors git.
Plan : [[PLAN]] (étage 3). Licence du jeu **CC-BY-NC-ND** : recherche uniquement, pas de modèle commercial.

## Le jeu
BNCI 2015-013 (Chavarriaga & Millán, EPFL, 2010). 6 sujets × 2 sessions × 10 blocs, Biosemi 64 canaux à 512 Hz.
Le sujet **ne commande rien** : il regarde un curseur qui va vers une cible et se trompe de sens environ 20 % du temps.
C'est presque notre protocole « regarder un robot se tromper ».
- Codes (description officielle, bits) : **5 et 10 = correct, 6 et 9 = erreur** ; 1, 2, 4, 8 ignorés.
- Écart entre les 2 sessions : 51, 50, 54, 211, **628, 643** jours (sujets 1 à 6).
- Après prétraitement : **6 437 essais, 20,5 % d'erreurs** (16,5 à 24,1 % selon la session).
- Prétraitement : référence moyenne, 1-10 Hz, époque 0-800 ms, 64 Hz.

## Témoin positif : la forme de l'ErrP (FCz, erreur moins correct)
Moyenne : creux **-2,5 µV à 266 ms**, pic **+5,0 µV à 328 ms** (article : ~270 et ~350 ms). Étiquettes confirmées.
Sujet 4 : pas de creux net (+0,9 µV), seulement le pic. Les cerveaux ne réagissent pas tous pareil.

## Modèle (identique pour les deux cas)
xDAWN (4 filtres) + espace tangent riemannien + régression logistique (classes équilibrées).
**Canal Iz retiré** : après la référence moyenne, les 64 canaux sont de rang 63 (valeur propre min ~1e-13), la
covariance d'xDAWN n'est pas inversible (LinAlgError au pli inter-sujets 2). Retirer un canal ne perd aucune
information. Essai écarté : régulariser xDAWN (`xdawn_estimator="lwf"`) sauvait le pli mais faisait tomber le cas
calibré de 0,82 à 0,74, ce qui aurait faussé la comparaison.

## Résultats (2026-11-08)

| Sujet | Écart sessions (j) | CALIBRÉ : AUC (préc. éq.) | SANS CALIBRATION : AUC (préc. éq.) |
|---|---|---|---|
| 1 | 51 | 0,935 (0,830) | 0,849 (0,746) |
| 2 | 50 | 0,934 (0,812) | 0,905 (0,818) |
| 3 | 54 | 0,847 (0,740) | 0,887 (0,817) |
| 4 | 211 | 0,752 (0,662) | 0,852 (0,754) |
| 5 | 628 | 0,855 (0,729) | 0,832 (0,762) |
| 6 | 643 | 0,686 (0,579) | 0,764 (0,681) |
| **Moyenne** | | **0,835 ± 0,091 (0,725)** | **0,848 ± 0,045 (0,763)** |
| Témoin mélangé | | 0,525 | 0,504 |

- CALIBRÉ = entraînement session 1 du sujet (~520 essais, 489 à 565), test sa session 2.
- SANS CALIBRATION = entraînement sur les 5 autres sujets (~5 300 essais), test sur les 2 sessions du sujet absent.
  Garde anti-fuite dans `bench.py` (sujet testé absent de l'entraînement), test exact `test_splits.py` + cassure
  (entraînement = tout) qui le fait rougir.

**Lecture.** Sur un cerveau jamais vu, l'ErrP se détecte à AUC 0,85, aussi bien qu'avec la calibration, et de façon
plus régulière (écart-type deux fois plus petit ; le sujet 6 passe de 0,69 à 0,76).
**Pourquoi « sans calibration » n'est pas sous le calibré ici** : le cas calibré n'est pas une vraie borne haute.
Il apprend sur peu d'essais d'une seule séance, puis il est testé des semaines à 2 ans plus tard, quand le signal a
dérivé. Le modèle des 5 autres a vu 10 fois plus d'essais et 10 séances : il apprend la forme commune de l'ErrP.
**Ce que ça ne prouve pas** : 6 sujets seulement, gel et 64 électrodes Biosemi en labo, curseur et non robot.
Filtre à phase nulle (il regarde en avant dans le temps) : résultats HORS LIGNE ; un filtre causal en temps réel
retardera et déformera l'ErrP, à remesurer.
Le transfert vers des électrodes sèches sur le device UBRI reste entièrement à mesurer.

## Résultat 3 : électrodes du device UBRI, sans calibration (2026-11-08)
Mode `bench.py device`, mêmes plis inter-sujets. Deux variantes, parce que le device ne peut pas calculer la
référence moyenne sur 64 électrodes :
- **A (optimiste)** : on garde les électrodes, mais référencées sur les 64 du casque de labo.
- **B (réaliste)** : re-référencées à leur propre moyenne (ce que le device peut faire), puis Fz retiré (rang).

| Électrodes | A : AUC (préc. éq.) | **B réaliste : AUC (préc. éq.)** |
|---|---|---|
| 64 (casque labo, rappel) | 0,848 (0,763) | |
| 7 : Fz FCz Cz CPz Pz C3 C4 | 0,802 (0,727) | **0,786 ± 0,056 (0,713)** |
| 3 : Fz FCz Cz | 0,778 (0,711) | **0,673 ± 0,092 (0,618)** |

**Lecture.**
- Avec 7 électrodes, on garde l'essentiel : AUC 0,79 en réaliste, contre 0,85 avec 64. La perte vient surtout du
  nombre d'électrodes (0,85 vers 0,80), un peu de la référence (0,80 vers 0,79).
- Avec 3 électrodes, la référence fait tout : 0,78 en optimiste, mais 0,67 en réaliste. Trois points proches les uns
  des autres, référencés entre eux, s'annulent en partie : l'ErrP est commun aux trois.
- Conséquence pour le device V3 : **viser au moins 7 électrodes, réparties de l'avant (Fz) à l'arrière (Pz) et sur
  les côtés (C3, C4)**, pas un amas autour de FCz.

**Témoin.** Avec la graine 0, le témoin mélangé tombait à 0,43-0,44 dans les 4 configurations (un seul tirage
partagé). Rejoué sur 5 autres graines (device7 B) : 0,475 / 0,496 / 0,529 / 0,530 / 0,538, moyenne ~0,51. Pas de
biais. Mais un pli isolé varie de **0,39 à 0,64 sous le hasard** : ne jamais lire un écart de quelques centièmes sur
un seul sujet.

**Limites.** Ce sont des électrodes à gel d'un Biosemi actif. Des électrodes sèches à travers les cheveux auront
plus de bruit et d'artefacts de mouvement : 0,79 est un plafond pour le device, pas une prévision.

## Résultat 4 : courbe de calibration (2026-11-08)
Mode `bench.py calib`. Calibration = les k PREMIERS essais du nouveau sujet (ordre chronologique, vérifié contigu).
Test = ses essais après le 200e, **identiques pour tous les k**. Durée mesurée : **3,5 s par essai** (blocs de
182 s pour 52 essais), donc 200 essais ≈ 12 min. « Mélange » = 5 autres sujets + k essais ; « sujet seul » = k essais.

| k (durée) | device7 réaliste : mélange | device7 : sujet seul | 64 él. : mélange | 64 él. : sujet seul |
|---|---|---|---|---|
| 0 | **0,781** | - | 0,846 | - |
| 10 (35 s) | 0,782 | 0,709 (5/6 sujets) | 0,846 | 0,576 (5/6) |
| 25 (1,5 min) | 0,783 | 0,734 | 0,847 | 0,665 |
| 50 (3 min) | 0,785 | 0,782 | 0,849 | 0,725 |
| 100 (6 min) | 0,789 | **0,801** | 0,855 | 0,777 |
| 200 (12 min) | 0,794 | **0,820** | 0,861 | 0,828 |

Témoins mélangés : 0,43 à 0,53 (graine 0 basse en device7, déjà vue au résultat 3).

**Lecture.**
- **Le mélange naïf gaspille la calibration** : 200 essais du sujet noyés parmi ~5 300 des autres ne font gagner
  que 0,01. Le modèle ne « voit » presque pas le nouvel utilisateur.
- **Sur le device, 6 minutes du sujet seul battent déjà le transfert** (0,80 contre 0,78), et 12 minutes donnent
  0,82. Avec 6 électrodes, le modèle a peu de paramètres, il apprend vite. Avec 64, il faut 12 minutes pour égaler.
- Donc la bonne recette produit est probablement : **démarrer avec le modèle générique (0,78 dès la première
  seconde), puis l'adapter à la personne**. Mais pas en mélangeant les essais tels quels.

**Prochaine mesure (la plus utile).** Adaptation qui donne du poids au nouvel utilisateur :
(a) **recentrage riemannien** (Zanini et al. 2018) : chaque personne recentrée sur sa propre covariance moyenne,
qui se calcule **sans étiquettes**, donc sans calibration consciente ; (b) pondération des essais du sujet.
Question : peut-on atteindre ~0,82 avec 0 à 3 minutes au lieu de 12 ?

## Résultat 5 : adapter le modèle au nouvel utilisateur (2026-11-08)
Mode `bench.py adapt`, device 7 électrodes réaliste. xDAWN appris sur les 5 autres seulement. Test identique au
résultat 4 (essais après le 200e). P = k essais étiquetés ajoutés avec un poids total égal à celui d'un sujet moyen ;
R = recentrage riemannien sans étiquettes (moyenne des k premiers essais) ; borne R = moyenne sur TOUS ses essais.

| k (durée) | générique | P pondéré | R recentré | **R+P** | sujet seul (rés. 4) | témoin R+P |
|---|---|---|---|---|---|---|
| 0 | 0,781 | | | | | |
| 10 (35 s) | | 0,798 | 0,777 | **0,796** | 0,709 | 0,480 |
| 25 (1,5 min) | | 0,800 | 0,778 | **0,803** | 0,734 | 0,537 |
| 50 (3 min) | | 0,810 | 0,778 | **0,815** | 0,782 | 0,490 |
| 100 (6 min) | | 0,814 | 0,779 | **0,818** | 0,801 | 0,470 |
| 200 (12 min) | | 0,817 | 0,780 | **0,821** | 0,820 | 0,533 |
Borne R (recentrage sur tous les essais, irréaliste) : 0,779.

**Lecture.**
- **La pondération marche** : 3 minutes donnent 0,815, ce que le sujet seul n'atteint qu'en 12 minutes. 35 secondes
  donnent déjà 0,80. Le gain est sur les courtes calibrations ; à 12 minutes, P, R+P et sujet seul se rejoignent (~0,82), R reste à 0,78.
- **Le recentrage seul n'apporte rien ici** (0,78, même avec tous les essais). Mesuré, pas expliqué. Hypothèses à
  vérifier : sur ce jeu (même casque, même labo), les cerveaux diffèrent peu par la puissance globale, qui est ce
  que le recentrage corrige ; et les filtres xDAWN appris sur les autres ont déjà fait l'essentiel. Avec des
  électrodes sèches, dont le contact change à chaque pose, il pourrait compter beaucoup plus : à remesurer.
- **Plafond de ce modèle sur le device : ~0,82.** Pour aller au-delà, il faudra un meilleur modèle ou de meilleures
  données, pas plus de calibration.

**Phrase pour le pitch (bornée)** : « Sur un jeu public de 6 personnes, avec seulement les 7 électrodes que le device UBRI
atteint, le signal d'erreur se détecte à AUC 0,78 sans aucune calibration, et 0,81 après 3 minutes. »
Toujours préciser : électrodes à gel en labo, curseur et non robot.

## Résultat 6 : simulation d'électrodes sèches (2026-11-08)
Mode `bench.py degrade`. Device 7 électrodes réaliste. Scénario de la phase 0 : modèle appris sur les 5 autres
**propres** (gel, comme les jeux publics), nouvel utilisateur **bruité** (calibration et test). Bruit gaussien filtré
1-10 Hz, indépendant par électrode, ajouté AVANT la re-référence. Niveau = écart-type du bruit / écart-type du signal
de l'électrode (le « signal » est tout l'EEG de l'époque dans la bande, pas seulement l'ErrP). Calibration : 3 min.

| Scénario (sujet testé) | générique | R recentré (3 min) | **R+P (3 min)** | témoin |
|---|---|---|---|---|
| propre (contrôle = résultat 5) | 0,781 | 0,778 | **0,815** | 0,418 |
| bruit 0,5× | 0,718 | 0,715 | **0,735** | 0,448 |
| bruit 1× | 0,656 | 0,650 | **0,671** | 0,451 |
| bruit 2× | 0,569 | 0,563 | 0,580 | 0,492 |
| bruit 3× | 0,535 | 0,529 | 0,524 | 0,487 |
| 1 électrode en mauvais contact (5×) | 0,738 | 0,726 | **0,767** | 0,446 |

Contrôle : la ligne « propre » retrouve exactement les chiffres du résultat 5. Témoin bas (0,42-0,45) avec la
graine 0. Ce témoin garde xDAWN appris sur les vraies étiquettes (seule la régression est mélangée), il n'est donc
pas celui du résultat 3 : sa dispersion est mesurée par `outils/temoin_agreg.py` (n = 1 : 0,42 à 0,54 sur 6 graines,
centré sur 0,50 ; voir résultat 7).

**Lecture.**
- **Le bruit est le vrai risque du device** : dès que le bruit égale l'EEG de fond (1×), on tombe à 0,66 ; à 2×,
  on est presque au hasard. Il n'y a presque pas de marge.
- **La calibration ne rattrape pas le bruit** (+0,02 environ) : le bruit détruit de l'information, il ne la déplace pas.
- **Le recentrage ne sert pas non plus ici** : l'hypothèse du résultat 5 (« il comptera avec des électrodes
  sèches ») : **pas de gain mesuré pour un bruit additif** (6 sujets, un seul modèle de bruit). Elle reste ouverte pour un changement de contact (gain, décalage),
  qui n'est pas simulé.
- **Un mauvais contact coûte peu** (0,74 au lieu de 0,78). 3 min de calibration donnent 0,77, mais 0,815 avec tous
  les contacts bons : la calibration ajoute son gain habituel, elle ne rattrape pas le contact.
  Sept électrodes réparties donnent de la redondance.

**Conséquences pour le device V3 (exigences, pas mesures).** Reprises dans [[hardware/exigences-device-v3]].
1. Exigence de bruit : **bruit propre de l'électrode ≤ 0,5× l'EEG de fond dans 1-10 Hz** pour rester vers 0,72.
2. **Première mesure à faire sur le device** : son niveau de bruit pendant une tâche (écart-type de sèche − gel au
   même endroit, divisé par celui du gel, 1-10 Hz, sur les époques de l'essai). Ce tableau convertit ce chiffre en AUC
   attendue (définition exacte : DEV-10 de [[hardware/exigences-device-v3]]).
3. Détecter un mauvais contact pendant l'usage (qualité de signal par électrode) vaut la peine : il coûte ≈ 0,04 et
   la calibration ne le rattrape pas (0,767 contre 0,815 avec tous les contacts bons).
4. Piste à mesurer : agréger plusieurs erreurs d'un même épisode robot plutôt que juger essai par essai.

**Limites du modèle de bruit.** Bruit blanc dans la bande, gaussien, stationnaire, indépendant entre électrodes. Le
bruit réel d'une électrode sèche est plutôt concentré en basses fréquences, lié au mouvement (donc corrélé entre
électrodes et non stationnaire), avec des sauts de contact. Et le bruit est filtré sur des époques de 0,8 s,
plus courtes que le filtre 1 Hz (MNE complète le signal) : « filtré 1-10 Hz » est approché. Ce tableau donne un ordre de grandeur, pas une prévision.

## Résultat 7 : agréger plusieurs observations d'un même événement (2026-11-08)
Mode `bench.py agreg` (mêmes scores que `degrade`, device 7 électrodes réaliste, entraînement propre). Le jeu n'a pas
d'épisodes de robot : on simule **un même événement observé n fois** en moyennant les scores (logit) de n essais
distincts de la même classe, 2 000 événements par classe. Deux variantes :
- **même personne, n visionnages** : n essais du sujet testé (optimiste : pas d'habituation simulée) ;
- **n personnes différentes** : un essai chez n sujets distincts, chacun noté par son modèle inter-sujets (n ≤ 6).
Contrôle : à n = 1 on retrouve l'AUC par essai (0,778 contre 0,781). Fonctions vérifiées aussi sur des scores
synthétiques (n = 4 : 0,85 pour 0,87 en théorie ; aucun signal : 0,49).

AUC par événement (générique = sans calibration ; R+P = 3 min de calibration) :

| Sujet testé | Méthode | n=1 | n=2 | n=3 | n=5 | n=10 | 5 personnes |
|---|---|---|---|---|---|---|---|
| propre | générique | 0,778 | 0,858 | 0,898 | 0,946 | 0,984 | 0,954 |
| propre | R+P | 0,816 | 0,892 | 0,935 | 0,972 | 0,995 | 0,976 |
| bruit 0,5× | générique | 0,719 | 0,788 | 0,836 | 0,893 | 0,954 | 0,909 |
| bruit 0,5× | R+P | 0,736 | 0,811 | 0,862 | 0,915 | 0,968 | 0,922 |
| bruit 1× | générique | 0,660 | 0,717 | 0,756 | 0,813 | 0,892 | 0,809 |
| bruit 1× | R+P | 0,671 | 0,732 | 0,775 | 0,830 | 0,906 | 0,843 |
| bruit 2× | générique | 0,565 | 0,595 | 0,621 | 0,654 | 0,700 | 0,654 |
| bruit 2× | R+P | 0,584 | 0,607 | 0,637 | 0,675 | 0,732 | 0,668 |

**Témoin : sa dispersion grandit avec n.** Le témoin garde les filtres xDAWN appris sur les vraies étiquettes (seule
la régression est mélangée) : il projette au hasard dans un espace qui contient l'ErrP, donc il penche d'un côté ou
de l'autre selon la graine, et l'agrégation amplifie ce penchant. Mesuré sur 6 graines
(`outils/temoin_agreg.py`, propre, générique) : **n = 1 : 0,42 à 0,54 ; n = 5 : 0,33 à 0,57 ; n = 10 : 0,28 à
0,60**, centré sur 0,50. Un résultat agrégé ne compte que s'il sort nettement de cette bande.

**Lecture.**
- **L'agrégation redonne la marge que le bruit enlève.** Avec un bruit égal à l'EEG de fond (1×), 5 observations
  remontent de 0,66 à 0,81-0,84, et 10 à 0,89-0,91. À 0,5×, 3 observations suffisent à dépasser le propre essai par
  essai (0,84-0,86 contre 0,78).
- **Plusieurs personnes valent plusieurs visionnages** (5 personnes ≈ 5 visionnages) dans cette simulation, et
  évitent l'habituation (dépendance entre personnes non simulée, voir Limites).
  Pour un jeu UBRI, ça plaide pour **plusieurs annotateurs par épisode** plutôt qu'un seul attentif.
- **À 2×, même 10 observations (0,70-0,73) ne sortent de la bande du témoin agrégé que de ~0,1** (bande jusqu'à
  0,60 à n = 10, mesurée sur le propre, pas à 2×) : conclusion fragile, le device doit rester sous 1×.

**Conséquence pour le produit.** Le format de données peut viser l'**épisode** (plusieurs observations, ou
plusieurs personnes par épisode) plutôt que l'essai isolé. Exigence révisée pour le device V3 : bruit ≤ 1× l'EEG de
fond, avec 3 à 5 observations par événement.

**Limites.** Observations supposées indépendantes à classe donnée, et tirées d'événements DIFFÉRENTS : optimiste
dans les deux variantes. Des personnes qui voient le MÊME événement partagent sa saillance (taille, surprise de
l'erreur) et leurs modèles partagent 4 sujets d'entraînement sur 5 ; la même personne s'habitue en plus (l'ErrP
s'émousse quand on revoit une erreur attendue). Les AUC agrégées sont des plafonds. Bruit simulé (voir résultat 6). Et une
annotation ErrP n'est jamais une observation unique : le robot doit refaire ou rejouer, ce qui a un coût.

## Résultat 8 : artefacts réalistes et nettoyage ciblé (2026-11-09)
Script `artefacts.py`, sortie `results/artefacts-v2.txt` (toutes les grandeurs ci-dessous en viennent). Question
posée par une critique publique : « avec un nettoyage ciblé sur les propriétés connues des artefacts, une poignée
d'électrodes suffit ». Le résultat 6 ne simulait qu'un bruit blanc ; ici, trois artefacts **simulés** (amplitudes
et taux choisis d'après les ordres de grandeur publiés, pas mesurés) sont ajoutés sur le sujet testé, device
7 électrodes réaliste, entraînement propre. EEG de fond : 4,8 à 7,4 µV d'écart-type selon l'électrode.
- **clignements** : 80-200 µV à Fp1, propagés vers l'arrière (Fz 0,45 → Pz 0,08), 0,3 par seconde : 32 % des
  époques portent plus de 15 µV à Fz, le décile le plus fort plus de 50 µV ;
- **sauts d'électrode** (contact sec, mouvement) : 30-100 µV sur une seule électrode, décroissance de 0,2 à 0,6 s :
  24 % des époques ont un saut de plus de 25 µV ;
- **muscle** (mâchoire, tempes) : bouffées de 15 à 150 Hz, 5,8 µV efficaces injectés à C3.

Nettoyage **sans étiquettes**, appris sur les 200 premiers essais du sujet (jamais testés) : **rejet** (époque
écartée si une électrode dépasse 3 fois son crête-à-crête médian) ; **ciblé** (électrode sautée reconstruite par
régression sur les 6 autres ; topographie du clignement estimée sur les époques détectées, puis retirée par
projection spatiale, à condition d'en détecter au moins 5) ; les deux. Scénario **piège** : on cligne plus après
une erreur (60 % des erreurs suivies d'un clignement), sur tous les sujets, entraînement compris.

Moyenne sur les 6 sujets (la dispersion entre sujets est de ±0,04 à ±0,07 : lire aussi le tableau par sujet) :

| Scénario | aucun | rejet (époques gardées) | ciblé | ciblé + rejet | témoin |
|---|---|---|---|---|---|
| propre | 0,781 | 0,783 (98 %) | 0,781 | 0,783 | 0,433 |
| clignements | 0,775 | 0,777 (89 %) | 0,775 | 0,780 | 0,452 |
| sauts d'électrode | 0,750 | 0,774 (80 %) | 0,755 | 0,764 | 0,444 |
| muscle | 0,781 | 0,783 (98 %) | 0,781 | 0,783 | 0,433 |
| les trois | 0,742 | 0,759 (75 %) | 0,741 | 0,753 | 0,455 |
| piège : clignements liés aux erreurs | **0,859** | 0,829 (85 %) | 0,781 | 0,784 | 0,457 |

Écart à « propre » du même sujet (sujets 1 à 6) :

| Scénario | aucun | rejet | ciblé | projection |
|---|---|---|---|---|
| clignements | −0,019 à +0,002 | −0,016 à +0,006 | **−0,029 à +0,037** | 5/6 sujets |
| sauts | −0,046 à −0,018 | −0,037 à +0,010 | −0,045 à −0,014 | sans objet |
| muscle | 0,000 partout | −0,002 à +0,014 | 0,000 à +0,002 | sans objet |
| piège | **+0,039 à +0,098** | +0,026 à +0,070 | **−0,049 à +0,075** | 5/6 sujets |

Dans le piège, le ciblé donne : sujets 1, 2, 3 ramenés près du propre (+0,002, +0,006, −0,011) ; sujets 4 et 5
**sous** le propre (−0,049 et −0,024 : la projection retire aussi de l'ErrP) ; sujet 6 **pas nettoyé du tout** (un
seul clignement détecté en calibration, pas de projection : +0,075, le piège passe). La moyenne de 0,781 vient de
ces erreurs qui se compensent, pas d'un nettoyage juste.

Détection contrôlée contre la vérité injectée : clignements, précision 100 %, rappel 28-42 % ; sauts, précision
94-95 %, rappel 43-57 %. Fausses alarmes sur données sans l'artefact : 1 clignement « vu » sur le propre, 11 sur les
sauts ; 6 sauts « vus » sur le propre. Témoin (chaîne entière, xDAWN compris, sur étiquettes mélangées, une seule
graine) entre 0,43 et 0,46 : ce n'est pas le témoin du résultat 7 (xDAWN sur vraies étiquettes), sa dispersion
n'est pas mesurée.

**Lecture.**
- **La critique a raison sur les artefacts NON liés à l'événement.** Le muscle disparaît dans le passe-bande 1-10 Hz
  (5,8 µV injectés, 0,04 µV restants). S'il commençait à 5 Hz au lieu de 15, il resterait 1,16 µV, soit 0,24 × le
  fond, et seulement pendant les bouffées. Les clignements coûtent au plus 0,02 par sujet. Les sauts coûtent 0,02 à
  0,05 ; le rejet en rattrape l'essentiel sur 4 sujets sur 6, au prix d'un essai sur cinq. Aucun n'approche le coût
  d'un bruit de contact égal à l'EEG de fond (0,66 au résultat 6).
- **Le vrai danger est le piège : un artefact lié à l'événement fabrique un faux succès.** Si l'on cligne plus après
  une erreur, l'AUC monte de 0,04 à 0,10 selon le sujet, sans rien de neuronal. Le rejet n'en retire qu'environ 40 %.
- **Notre nettoyage sans étiquettes ne suffit pas à s'en protéger.** Le détecteur, réglé sur le crête-à-crête du
  sujet lui-même, voit 0 ou 1 clignement chez le sujet 6 et rate 58 à 72 % des clignements ; quand la projection
  s'applique, elle coûte jusqu'à 0,05 d'ErrP. Avec des clignements NON liés à l'erreur, elle reste aussi variable
  d'un sujet à l'autre (−0,029 à +0,037).
- **Le goulot reste le bruit de contact dans la bande** (résultat 6), qui n'a pas de forme reconnaissable à retirer.

**Conséquences.**
- **Device V3 : la voie EOG est une question OUVERTE**, avec un candidat naturel : la 8e voie libre de l'ADS1299
  (une électrode frontale, Fp1 ou sous l'œil), qui donnerait au détecteur une référence indépendante du sujet. À
  évaluer au prochain banc : même scénario piège, détection par la voie EOG.
- **Contrôle obligatoire avant d'annoncer un score** : vérifier que la décision du classifieur n'est pas corrélée aux
  artefacts détectés (comparer l'AUC sur les époques avec et sans clignement).
- **Jeu de données UBRI** : chaque époque garde sa trace d'artefacts (clignement détecté, électrode reconstruite),
  pour qu'un acheteur puisse vérifier qu'un modèle ne lit pas l'œil.

**Limites.** Artefacts simulés : formes, amplitudes et taux choisis, sans corrélation entre artefacts ni avec la
fatigue. La topographie du clignement est identique pour toutes les époques et tous les sujets (rang 1 exact) : la
projection est dans son cas idéal, et la topographie du sujet testé ne nettoie l'entraînement que pour cette raison ;
le réel sera moins favorable. Le passe-bande est appliqué à des époques complétées (comme au résultat 6). Le témoin
n'a qu'une graine. Le piège ne teste qu'un artefact lié à l'erreur ; un mouvement de tête ou un regard vers l'erreur
en sont d'autres. Un premier passage (`artefacts-v1.txt`, non conservé) n'affichait l'état de la projection que pour
le sujet 1 et gardait, dans « ciblé + rejet », des époques de clignement non retirées : il annonçait à tort un retour
« exact » au propre.

## Suite prévue
1. ~~Inter-sujets~~ : fait, AUC 0,848.
2. ~~Courbe de calibration~~ : fait (résultat 4).
3. ~~Moins d'électrodes~~ : fait (7 électrodes réalistes : AUC 0,786).
4. ~~Adaptation~~ : fait (résultat 5). Pondération utile, recentrage neutre sur ce jeu.
5. ~~Dégrader le signal~~ : fait (résultat 6). Suite : mesurer le bruit réel du device V3 ; bruit de mouvement
   corrélé ; ~~agrégation~~ (fait, résultat 7) ; habituation de l'ErrP quand on revoit la même erreur.
