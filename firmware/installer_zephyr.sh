#!/usr/bin/env bash
# Installe l'environnement de compilation Zephyr pour la carte UBRI (nRF52840), sur hôte Linux aarch64 ou x86-64.
# Versions FIGÉES (à changer ici seulement, et dans README.md) :
ZEPHYR_VERSION=v4.4.2      # publiée le 2026-08-07
SDK_VERSION=1.0.1          # publiée le 2026-03-25
# Résultat : ~/zephyrproject (espace west : zephyr + cmsis, cmsis_6, hal_nordic, mbedtls, tf-psa-crypto),
# ~/zephyr-sdk-$SDK_VERSION (cmake + GNU arm), ~/zephyrproject/.venv (west et dépendances Python).
# Ne supprime rien ; s'arrête au premier échec.
set -euo pipefail

ARCH=$(uname -m)
case "$ARCH" in
  aarch64) HOTE=linux-aarch64 ;;
  x86_64) HOTE=linux-x86_64 ;;
  *) echo "hôte $ARCH non prévu" >&2; exit 2 ;;
esac
WS="$HOME/zephyrproject"
SDK="$HOME/zephyr-sdk-$SDK_VERSION"
DL="$HOME/.cache/ubri-zephyr-dl-$SDK_VERSION"
BASE="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v$SDK_VERSION"
MINIMAL="zephyr-sdk-${SDK_VERSION}_${HOTE}_minimal.tar.xz"
TOOLCHAIN="toolchain_gnu_${HOTE}_arm-zephyr-eabi.tar.xz"
# Empreintes FIGÉES ici (KE#110) : le sha256.sum téléchargé vient de la même origine que les archives, il ne peut
# pas les garantir seul. Relevées le 2026-11-09 dans le sha256.sum de la publication v1.0.1.
declare -A EMPREINTE=(
  [zephyr-sdk-1.0.1_linux-aarch64_minimal.tar.xz]=d79c5bfc68e679488659bea289a4026e52a64f03338875c8c9c850fff13cee30
  [toolchain_gnu_linux-aarch64_arm-zephyr-eabi.tar.xz]=b9805b691f2f0a8926c92694cae378d05ba07b76abca745e216fcc52753cc4d6
  [zephyr-sdk-1.0.1_linux-x86_64_minimal.tar.xz]=ca9bc0ff66fafca1dac9d592a36d953cf16d096a9d09b1c0357f021cf9f6a7eb
  [toolchain_gnu_linux-x86_64_arm-zephyr-eabi.tar.xz]=21b85981cb5a1818d9bc53d82af80f208946ec038b982ff1907287572ed3a634
)

echo "== 1. SDK $SDK_VERSION ($HOTE) : téléchargement et vérification sha256"
mkdir -p "$DL"
cd "$DL"
for f in "$MINIMAL" "$TOOLCHAIN"; do
  attendu=${EMPREINTE[$f]:-}
  [ -n "$attendu" ] || { echo "aucune empreinte figée pour $f (changer SDK_VERSION exige de relever les empreintes)" >&2; exit 3; }
  if [ ! -s "$f" ]; then
    curl -fSL --retry 3 -o "$f.part" "$BASE/$f"
    mv "$f.part" "$f"
  fi
  if ! printf '%s  %s\n' "$attendu" "$f" | sha256sum -c -; then
    echo "empreinte FAUSSE pour $f : supprimer $DL/$f et relancer (jamais d'extraction d'une archive non vérifiée)" >&2
    exit 3
  fi
done

if [ ! -d "$SDK" ]; then
  echo "== 2. extraction du SDK dans $SDK"
  tar -xJf "$MINIMAL" -C "$HOME"
  [ -d "$SDK" ] || { echo "le bundle n'a pas créé $SDK" >&2; exit 4; }
fi
if [ ! -d "$SDK/gnu/arm-zephyr-eabi" ] && [ ! -d "$SDK/arm-zephyr-eabi" ]; then
  echo "== 3. extraction de la chaîne arm-zephyr-eabi"
  mkdir -p "$SDK/gnu"
  tar -xJf "$DL/$TOOLCHAIN" -C "$SDK/gnu"
fi
echo "== 4. enregistrement du SDK pour CMake"
(cd "$SDK" && ./setup.sh -c)

echo "== 5. espace west $WS figé sur Zephyr $ZEPHYR_VERSION"
mkdir -p "$WS"
[ -d "$WS/.venv" ] || python3 -m venv "$WS/.venv"
# shellcheck disable=SC1091
source "$WS/.venv/bin/activate"
pip install -q --upgrade pip west
cd "$WS"
[ -d "$WS/.west" ] || west init -m https://github.com/zephyrproject-rtos/zephyr --mr "$ZEPHYR_VERSION"
actuelle=$(git -C "$WS/zephyr" describe --tags 2>/dev/null || echo "?")
[ "$actuelle" = "$ZEPHYR_VERSION" ] || { echo "espace west sur $actuelle, attendu $ZEPHYR_VERSION : ne pas mélanger" >&2; exit 5; }
# mbedtls + tf-psa-crypto : exigés par l'appairage BLE (SMP, cryptographie PSA) de l'application
west config manifest.project-filter -- '-.*,+cmsis,+cmsis_6,+hal_nordic,+mbedtls,+tf-psa-crypto'
west update --narrow -o=--depth=1
west zephyr-export
pip install -q -r "$WS/zephyr/scripts/requirements-base.txt"
echo "== FAIT : $(cd "$WS/zephyr" && git describe --tags) ; SDK $(cat "$SDK/sdk_version" 2>/dev/null || echo "$SDK_VERSION")"
