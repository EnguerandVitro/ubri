# Dépendances tierces de products/ubri/nrd

| Composant | Version | Licence | Source | Vérification |
|---|---|---|---|---|
| liblsl (Lab Streaming Layer) | 1.17.7, `liblsl-1.17.7-jammy_arm64.tar.gz` | MIT | github.com/sccn/liblsl/releases | sha256 épinglé dans `bootstrap.sh` |
| Paquets Python | voir `requirements.txt` (versions épinglées) | voir chaque paquet | PyPI | pip |

`vendor/` et `.venv/` ne sont pas versionnés : `bash bootstrap.sh` les reconstruit.
