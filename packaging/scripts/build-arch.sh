#!/usr/bin/env bash
# Build an Arch Linux package for lucy-notes-daemon using makepkg.
# Requires: makepkg (Arch Linux host or container), python, python-pip, python-build.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT_DIR}/packaging/build/arch"
VERSION="$(bash "${ROOT_DIR}/packaging/common/version.sh")"
PKG_NAME="lucy-notes-daemon"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# 1. Build the Python wheel.
cd "${ROOT_DIR}"
python3 -m build --wheel --outdir "${BUILD_DIR}/wheel"
WHEEL="$(ls -1 "${BUILD_DIR}/wheel"/*.whl | head -n1)"

# 2. Copy sources into the build dir expected by makepkg.
cp "${WHEEL}" "${BUILD_DIR}/"
cp "${ROOT_DIR}/config.txt" "${BUILD_DIR}/config.txt"
cp "${ROOT_DIR}/packaging/common/lucy-notes-daemon.service" \
    "${BUILD_DIR}/lucy-notes-daemon.service"
cp "${ROOT_DIR}/packaging/arch/PKGBUILD.in" "${BUILD_DIR}/PKGBUILD"

# 3. Expand substitution variables in PKGBUILD.
WHEEL_BASENAME="$(basename "${WHEEL}")"
sed -i \
    -e "s|@VERSION@|${VERSION}|g" \
    -e "s|@WHEEL@|${WHEEL_BASENAME}|g" \
    "${BUILD_DIR}/PKGBUILD"

# 4. Build.
cd "${BUILD_DIR}"
if command -v makepkg >/dev/null 2>&1; then
    makepkg --nodeps --skipinteg -f
else
    echo "makepkg not found. PKGBUILD ready at ${BUILD_DIR}/PKGBUILD." >&2
    echo "Run 'makepkg --nodeps --skipinteg -f' from that directory on an Arch host." >&2
fi

echo "Arch build outputs in ${BUILD_DIR}"
