---
title: "UBRI-NRD : format ouvert de données neuronales ancrées au robot (v0.1, brouillon)"
date: 2026-11-07
tags: [ubri, donnees, format, standard, lerobot, bids]
status: brouillon (implémentation de référence : products/ubri/nrd)
---

# UBRI-NRD v0.1 : Neural-Robot Data

But (D18) : un format **ouvert** où chaque instant d'EEG est **ancré** à ce que la personne voyait et à ce que faisait le robot.
Il est fusionnable entre labos et chargeable par les outils existants. On ne réinvente rien : on **étend LeRobot v3** côté robot
et on **exporte en BIDS-EEG** côté neuro.

## Principes
1. **Une horloge commune.** Tous les flux (EEG, robot, caméra, événements) sont horodatés sur la même base de temps, via
   Lab Streaming Layer. La dérive d'horloge est mesurée et enregistrée par session.
2. **Brut d'abord.** L'EEG est conservé brut (µV, fréquence d'origine). Les caractéristiques dérivées vont dans des fichiers à part.
3. **Étiquetage par construction.** Chaque erreur du robot est **injectée et connue** du protocole : la vérité terrain ne dépend
   pas de l'EEG.
4. **Consentement dans la donnée.** Chaque session porte son niveau de consentement. Une session sans consentement de
   publication **ne peut pas** être exportée vers le jeu public (contrôle bloquant dans l'outil d'export).

## Arborescence (dataset LeRobot v3 + extension `neural/`)
```text
ubri-nrd-<nom>/
├── meta/
│   ├── info.json            # LeRobot v3 + bloc "ubri_nrd": {version, eeg_device, montage, fs, clock_sync}
│   ├── episodes/            # LeRobot v3 (bornes d'épisodes, index des vidéos)
│   ├── tasks.parquet        # LeRobot v3
│   ├── participants.parquet # id pseudonyme, âge par tranche, latéralité, consentement {collecte, publication, usage_commercial}
│   └── montage.json         # positions 10-20 utilisées, référence, BIAS, type d'électrode (sèche / gel)
├── data/                    # LeRobot v3 : état et action du robot, image par image (Parquet)
├── videos/                  # LeRobot v3 : caméras (MP4) dont une vue « ce que voit la personne »
└── neural/
    ├── eeg/chunk-XXX/file-YYY.parquet   # t (s, horloge commune), canaux en µV, qualité de contact par canal
    ├── events.parquet                   # t, episode_index, type, détail (voir plus bas)
    └── impedance.parquet                # contrôles de contact avant / pendant la session
```

## Événements (`neural/events.parquet`)
| type | sens | champs |
|---|---|---|
| `robot_action_start` / `robot_action_end` | début et fin d'une action du robot | `action_id` |
| `robot_error_injected` | le protocole fait se tromper le robot | `error_kind` (mauvais objet, mauvaise place, trajectoire), `planned: true` |
| `robot_correct` | action correcte (témoin) | |
| `stimulus` | stimulus P300 (son, flash) | `target: bool` |
| `participant_report` | confirmation explicite après coup (vérification hors EEG) | `saw_error: bool` |
| `artifact` | clignement, mouvement, perte de contact | `source` |
| `source_event` | événement d'un jeu converti, **non interprété** (on ne devine pas son sens) | `source_code` obligatoire |

Vocabulaire **fermé** : un type inconnu est une erreur de validation. `error_kind` ∈ {`mauvais_objet`, `mauvaise_place`,
`trajectoire`, `mauvaise_direction`}. Chaque événement garde `source_code` (code d'origine) quand il existe.

L'ErrP attendu se cherche dans la fenêtre 0-800 ms après `robot_error_injected` (pics typiques entre 200 et 500 ms). La fenêtre
reste un **paramètre d'analyse**, pas une donnée du format.

## Décisions prises en implémentant (2026-11-08)
- **Agent** : `info.json` déclare `agent` ∈ {`robot`, `curseur_ecran`}. Un jeu converti sans robot (BNCI 2015-013 : un
  curseur) reste lisible avec les mêmes outils, sans faire semblant d'avoir des données LeRobot.
- **Épisodes côté neuro** : `neural/episodes.parquet` (`episode_index, participant_id, session, run, t_start, t_end,
  n_samples, eeg_file`), un fichier EEG par épisode. La partie LeRobot (`data/`, `videos/`, `meta/episodes/`) viendra avec
  le premier enregistrement robot.
- **Brut au bit près** : l'EEG garde son type d'origine (float64 pour BNCI ; le device UBRI enregistrera des entiers 24 bits
  + facteur d'échelle). Mesuré : BNCI converti relu identique au bit près, 120 blocs.
- **Horloge** : `clock_sync` déclare honnêtement la méthode ; pour un jeu converti, « aucune » (blocs posés bout à bout).
- **Consentement** : l'export public a deux jambes indépendantes (filtre d'entrée, puis relecture de la sortie) ; un
  export où personne n'a consenti renvoie « rien à publier », jamais un succès vide. Sous une licence qui permet
  l'usage commercial (sans « -NC »), `consent_usage_commercial` est exigé en plus de `consent_publication` ;
  `consent_collecte` faux rend le jeu invalide (KE#283).
- **Chemins dérivés** : `eeg_file` doit valoir `neural/eeg/chunk-XXX/file-YYYYYY.parquet` calculé depuis
  `episode_index` ; liens symboliques refusés. Un épisode ne peut pas désigner le fichier d'un autre.
- **Champs fermés** : `details` n'accepte que les champs prévus pour son type ; `planned`, `target`, `saw_error` sont
  des booléens. Rien de libre ne part dans le jeu public.
- **Dérive d'horloge** : le pas d'échantillonnage est comparé à 1/fs nominal avec 1 000 ppm de tolérance (un quartz
  réel dérive de ±100 ppm) ; la dérive mesurée est déclarée dans `clock_sync`.
- Taille mesurée : BNCI converti (64 canaux, 512 Hz, float64, ~2 h) = 1,9 Go en Parquet (un fichier par bloc).

## Export BIDS-EEG
Script d'export : `neural/eeg` → `sub-XX/ses-YY/eeg/*_eeg.edf` + `*_events.tsv` (depuis `events.parquet`) + `*_channels.tsv` +
`*_electrodes.tsv`, pour que les outils neuro (MNE, MOABB, EEGDash) le lisent sans adaptation.

## À trancher
- [x] Granularité de l'EEG : un fichier par épisode (v0.1). Mesuré sur BNCI : 1,9 Go, 120 fichiers. À revoir pour des sessions longues.
- [ ] Licence du jeu public : CC-BY-4.0 (réutilisable par les labos) ou une licence qui exclut l'usage commercial ?
- [ ] Faut-il publier le flux vidéo « ce que voit la personne » ? Il peut montrer des visages ou des lieux : à flouter.
