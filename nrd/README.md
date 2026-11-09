# UBRI-NRD : implémentation de référence du format (v0.1)

Spec : `vault/01-projects/ubri/spec-format-donnees-v0.1.md`.

```bash
bash bootstrap.sh                                   # venv + liblsl épinglée (sha256), rien dans le système
.venv/bin/python -B -m ubri_nrd.valider <jeu>       # 0 = valide, 1 = erreurs nommées
.venv/bin/python -B -m ubri_nrd.exporter <jeu> <dossier neuf>   # 0 exporté, 3 rien à publier, 1 refus
.venv/bin/python -B convertir_bnci.py sorties/bnci2015013-nrd   # jeu LOCAL (licence ND : jamais publié)
.venv/bin/python -B tests/test_nrd.py [--bnci sorties/bnci2015013-nrd]
PYLSL_LIB=$PWD/vendor/liblsl/lib/liblsl.so .venv/bin/python -B lsl/banc_lsl.py sorties/lsl-x [--duree 120] [--drift 500] [--sans-calibration]
```

- `ubri_nrd/schema.py` : vocabulaire fermé (types d'événements, champs, consentement).
- `ubri_nrd/io.py` : écriture atomique (`.tmp` + `os.replace`), `info.json` écrit en dernier.
- `ubri_nrd/valider.py` : chaque contrôle annonce son compteur ; chaque erreur nomme fichier, épisode, champ.
- `ubri_nrd/exporter.py` : export public, deux jambes indépendantes sur le consentement.
- `sorties/`, `.venv/`, `vendor/` : hors git.

## v0.2 (2026-11-09) : trous dans l'EEG
Colonne `idx` (compteur de l'appareil) dans chaque fichier EEG, `idx_start` / `idx_end` / `pertes_annoncees` par
épisode, `ecrire_eeg(..., *, idx)` obligatoire, `lire_eeg_grille()` pour une grille régulière avec NaN. Spec :
`vault/01-projects/ubri/spec-format-donnees-v0.2.md`. L'ancien jeu BNCI v0.1 est gardé localement dans
`sorties/bnci2015013-nrd-v01` (le validateur v0.2 le refuse : « à reconvertir »).
