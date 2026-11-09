---
title: "UBRI-NRD v0.2 : les trous dans l'EEG"
date: 2026-11-09
tags: [ubri, format, nrd, donnees, phase-0]
status: fait
---

# UBRI-NRD v0.2 : représenter les trous dans l'EEG

Delta par rapport à [[spec-format-donnees-v0.1]] (tout le reste est inchangé). Code : `products/ubri/nrd/`
(`ubri_nrd/schema.py`, `io.py`, `valider.py`), tests `tests/test_nrd.py`.

## Pourquoi
En v0.1, le validateur exigeait un pas régulier entre deux échantillons consécutifs : une seule trame perdue
rendait le jeu invalide, et le banc Connect n'écrivait aucun jeu dès qu'une liaison perdait des paquets
([[connect]]). Une vraie liaison Bluetooth perd des paquets : le format doit savoir le dire.

## Ce qui change
1. **Fichier EEG** : colonnes `t`, **`idx`**, puis les voies du montage. `idx` est le **compteur d'échantillon de
   l'appareil** (entier 64 bits, strictement croissant). Seuls les échantillons **reçus** sont écrits : un trou se
   lit comme un saut de `idx`. Les valeurs restent brutes, dans leur type d'origine.
2. **`episodes.parquet`** : trois colonnes de plus.
   | Colonne | Sens |
   |---|---|
   | `idx_start`, `idx_end` | étendue **annoncée par la source** (trame d'ouverture : compteur de départ ; trame de clôture : dernier compteur), échantillons manquants compris |
   | `pertes_annoncees` | pertes **déclarées par la source** (clôture : DRDY manquées + trames abandonnées), entier Int64 nullable (jamais un flottant) ; **vide** si la source n'annonce rien (jeu converti, ancienne trame). **Jamais calculé par l'hôte** à partir des compteurs reçus : ce serait l'identité de KE#286 |
   `n_samples` reste le nombre d'échantillons **présents** dans le fichier. `t_start` / `t_end` deviennent l'heure
   de `idx_start` / `idx_end`, même si ces échantillons manquent (extrapolée par la droite t ~ idx).
3. **`ecrire_eeg(..., *, idx)`** : `idx` est obligatoire, sans valeur par défaut (KE#62). Un producteur qui
   l'oublie plante (`TypeError`) au lieu de déclarer un flux continu par omission.
4. **Lecture en grille** : `lire_eeg_grille(root, episode, voies)` rend la grille régulière `idx_start..idx_end`,
   valeurs à NaN et `present = False` aux manquants, heures des manquants retrouvées par la droite t ~ idx. Le
   fichier, lui, reste brut.

**Pourquoi pas des NaN dans le fichier** : une grille remplie de NaN perd le type brut, rend un échantillon absent
indiscernable d'une valeur invalide, et ne dit pas combien d'échantillons manquent ni où. Un compteur qui saute le
dit exactement.

## Ce que contrôle le validateur (en plus de la v0.1)
- `idx` entier, strictement croissant, compris dans `[idx_start, idx_end]` ; `idx_start ≤ idx_end`.
- **Pas d'échantillonnage mesuré par échantillon d'appareil** : `Δt / Δidx` comparé à 1/fs (1 000 ppm), donc
  valable **à travers un trou**. Limite : un décalage d'heure après un trou passe tant qu'il reste sous 1 000 ppm de
  la durée du trou (10 ms après un trou de 10 s) ; les producteurs actuels écrivent `t = a0 + pas · idx`, donc ce
  cas ne se présente pas.
- `t_start` / `t_end` cohérents avec `idx` (tolérance proportionnelle à la distance extrapolée).
- **Manquants calculés** = `(idx_end − idx_start + 1) − présents`. Si `pertes_annoncees` est renseigné :
  manquants **<** annoncées = erreur (annonce ou étendue fausse) ; manquants **>** annoncées = **pertes côté hôte**
  (pile Bluetooth, tampon LSL, décodeur : invisibles pour la carte), **comptées et affichées**, jeu valide. Le
  format enregistre la vérité ; l'égalité stricte reste un critère du banc Connect. Refuser le jeu aurait poussé à
  vider l'annonce, ce qui coupe le contrôle en silence (KE#105). Choix fait en revue, le 2026-11-09.
- Compteurs toujours affichés (KE#111) : échantillons manquants, trous (début, milieu, fin ; un épisode sans
  échantillon compte 1 trou), pertes non annoncées par la source (côté hôte), **événements dans un trou** = entre
  deux échantillons reçus non adjacents, trouvés par encadrement dans les horodatages, exact quelle que soit la
  dérive (KE#288 ; information, pas une erreur).
- Un jeu v0.1 est refusé avec la consigne « à reconvertir ».

## Tests et mesures (2026-11-09)
- `tests/test_nrd.py` : jeu sans trou (compteurs exacts) ; jeu avec 5 manquants en tête, 20 au milieu (sur un
  événement), 3 en queue, annoncés : **valide**, 28 manquants, 3 trous, 1 événement dans un trou ; appel sans `idx`
  = `TypeError` ; grille exacte, et refus d'un `idx` hors de l'étendue ; perte côté hôte (10 manquants, 0 annoncé)
  = valide avec 10 comptés ; événement dans un trou sous dérive +500 ppm sur 1 000 s : compte exact ; **32
  cassures** de données, chacune rouge sur son motif ; **6 mutations du code** sur copie jetable (`python -B`), toutes
  rouges : événements par fs nominal, garde de la grille, annonce > manquants, pertes côté hôte, étendue invalide,
  idx non int64.
- **BNCI 2015-013 reconverti** : 120 blocs, EEG identique au bit près, positions et codes identiques, valide ;
  pertes « non annoncées » (le fichier d'origine ne dit rien des pertes d'acquisition).
- **Banc Connect** (trame v3) : 20 % de paquets abandonnés + DRDY ratées : **jeu valide**, 6 203 manquants =
  pertes annoncées, 508 trous, 14 événements dans un trou (calcul avant le passage à l'encadrement). Cassure du
  diffuseur (trame jetée côté hôte) : jeu **valide** avec 1 perte non annoncée comptée, et le critère du banc
  « comptées = annoncées » **rouge**. Trame v2 : jeu valide, pertes non annoncées.
- **Banc LSL** : jeu valide.

## Limites
- Avec une source qui n'annonce pas ses pertes (`pertes_annoncees` vide), les pertes de tête et de queue ne se
  voient pas : l'étendue se limite alors aux compteurs reçus.
- Le compteur 32 bits de la trame qui repasse par 0 n'est pas déroulé dans `idx` (sessions de plus de 198 jours à
  250 Hz : hors sujet aujourd'hui, à traiter dans le producteur le jour venu).
- L'export BIDS prévu par la v0.1 devra traduire les trous (BIDS n'a pas de notion native d'échantillon manquant).
