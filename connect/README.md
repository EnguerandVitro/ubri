# UBRI Connect : acquisition et synchronisation (v0.1, en cours)

Brique « Connect » de l'architecture UBRI (D21) : carte EEG -> flux LSL horodatés -> jeu UBRI-NRD (brique Neural,
`products/ubri/nrd`). **Aucune bibliothèque de fabricant** : BrainFlow n'existe qu'en x86-64 dans sa roue PyPI, et la
carte UBRI sera la nôtre (D22). Chaque carte est une « source » derrière la même interface.

| Fichier | Rôle | État |
|---|---|---|
| `ubri_connect/cyton.py` | décodeur du protocole série OpenBCI Cyton (carte de RÉFÉRENCE pour valider la nôtre) | fait, testé |
| `ubri_connect/trame.py` | trame UBRI (v1, v2, **v3** lead-off) + trames d'OUVERTURE et de CLÔTURE de session, protocole de NOTRE carte | fait, testé (5 cassures par mutation) |
| `ubri_connect/chaine.py` | carte simulée (ouverture + trames v1/v2/v3 + clôture sur un tube, DRDY ratées, compteur timer/isr), diffuseur LSL, robot simulé, traitement (dejitter sur compteur, appariement par le temps) | fait |
| `banc_connect.py` | banc de bout en bout, pertes de paquets, cassures | fait |
| `ubri_connect/recepteur_ble.py` | récepteur Bluetooth de la carte UBRI v0 (bleak) : notifications -> tube -> le MÊME `chaine.diffuseur` (flux LSL à 8 voies + bilan de session) ; attend l'ouverture et la clôture, codes de sortie nommés | testé contre un faux client (10 cas + LSL réel, cassures par mutation), JAMAIS contre une carte |

Tests (Python 3, sans dépendance) : `python3 -B tests/test_cyton.py && python3 -B tests/test_trame.py` ; banc :
`python3 -B banc_protocoles.py --n 20000`.

Limite connue du protocole : un faux en-tête `0xA0` suivi, 32 octets plus loin, d'un octet `0xC0..0xC6` dans des
données corrompues donne un paquet accepté à tort ; seul le saut de numéro d'échantillon le trahit (compté en pertes).

## Trame UBRI v0 et banc comparatif
`ubri_connect/trame.py` (CRC-16, compteur 32 bits, état de synchro) ; `python3 -B tests/test_trame.py` ;
`python3 -B banc_protocoles.py --n 20000`. Résultats et lecture : `vault/01-projects/ubri/connect.md`.

Banc de bout en bout (environnement de `../nrd`) :
`PYLSL_LIB=../nrd/vendor/liblsl/lib/liblsl.so ../nrd/.venv/bin/python -B banc_connect.py sorties/x [--duree 120]
[--trame 1|2|3] [--perte 0.05] [--rafales-drdy 10] [--compteur-isr] [--ignorer-ouverture] [--casse-diffuseur] [--drift 50] [--mk-lat 0.002 --mk-jit 0.0003] [--sans-calibration] [--dejitter-indice]`.

## Récepteur Bluetooth (poste d'acquisition, Mac ou Linux)
```bash
pip install bleak pylsl numpy          # sur le poste qui a la radio ; ce serveur n'en a pas
python3 -m ubri_connect.recepteur_ble --duree 600 --tag s01      # depuis products/ubri/connect
```
Premier appairage : carte démarrée bouton 1 enfoncé (fenêtre de 60 s, liaisons précédentes effacées), **sans
électrode posée** ; le système demande la clé à six chiffres affichée sur la console de la carte. Ensuite, l'hôte lié
se reconnecte sans clé. Codes de sortie : 0 session bornée, 2 carte introuvable, 3 clôture non reçue (session non
bornée), 4 ouverture absente ou non conforme, 5 bleak absent, 6 diffuseur LSL arrêté ou octets non transmis
(bilan absent ou partiel), 7 erreur Bluetooth (appairage refusé hors fenêtre, liaison perdue). Tests :
`python3 -B tests/test_recepteur_ble.py` (le cas LSL réel avec l'environnement de `../nrd`, sinon annoncé SAUTÉ).
