#!/usr/bin/env bash
# Build a Debian/Ubuntu .deb package for lucy-notes-daemon.
#
# The package installs:
#   * the Python wheel into /opt/lucy-notes-daemon/ (using pip at install time)
#   * a launcher at /usr/bin/lucy-notes-daemon
#   * a default config at /etc/lucy-notes-daemon/config.txt
#   * a systemd user service
#
# Requires: python3, python3-build, dpkg-deb. Uses fpm when available.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT_DIR}/packaging/build"
STAGE_DIR="${BUILD_DIR}/deb-stage"
OUT_DIR="${BUILD_DIR}"

VERSION="$(bash "${ROOT_DIR}/packaging/common/version.sh")"
PKG_NAME="lucy-notes-daemon"

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}" "${OUT_DIR}"

# 1. Build the Python wheel.
cd "${ROOT_DIR}"
python3 -m build --wheel --outdir "${BUILD_DIR}/wheel"
WHEEL="$(ls -1 "${BUILD_DIR}/wheel"/*.whl | head -n1)"

# 2. Stage files.
install -d "${STAGE_DIR}/opt/lucy-notes-daemon"
install -d "${STAGE_DIR}/usr/bin"
install -d "${STAGE_DIR}/etc/lucy-notes-daemon"
install -d "${STAGE_DIR}/lib/systemd/user"

cp "${WHEEL}" "${STAGE_DIR}/opt/lucy-notes-daemon/"
cp "${ROOT_DIR}/config.txt" "${STAGE_DIR}/etc/lucy-notes-daemon/config.txt"
cp "${ROOT_DIR}/packaging/common/lucy-notes-daemon.service" \
    "${STAGE_DIR}/lib/systemd/user/lucy-notes-daemon.service"

cat > "${STAGE_DIR}/usr/bin/lucy-notes-daemon" <<'LAUNCHER'
#!/usr/bin/env bash
# Launcher for lucy-notes-daemon. Uses the virtualenv in /opt/lucy-notes-daemon.
exec /opt/lucy-notes-daemon/venv/bin/lucy-notes-daemon "$@"
LAUNCHER
chmod 0755 "${STAGE_DIR}/usr/bin/lucy-notes-daemon"

# 3. Maintainer scripts: create a venv and install the wheel on target host.
mkdir -p "${STAGE_DIR}/DEBIAN"

cat > "${STAGE_DIR}/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
VENV=/opt/lucy-notes-daemon/venv
WHEEL="$(ls -1 /opt/lucy-notes-daemon/*.whl | head -n1)"

if [ ! -x "${VENV}/bin/python" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/pip" install --upgrade pip >/dev/null
"${VENV}/bin/pip" install --upgrade "${WHEEL}"
POSTINST
chmod 0755 "${STAGE_DIR}/DEBIAN/postinst"

cat > "${STAGE_DIR}/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
rm -rf /opt/lucy-notes-daemon/venv || true
PRERM
chmod 0755 "${STAGE_DIR}/DEBIAN/prerm"

# 4. Control file.
cat > "${STAGE_DIR}/DEBIAN/control" <<CONTROL
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-venv, python3-pip
Maintainer: vvindetta <noreply@github.com>
Homepage: https://github.com/vvindetta/lucy_notes_daemon
Description: Modular notes manager daemon
 Lucy daemon watches your notes directories and runs modular processors
 (banner, renamer, todo-formatter, git sync, etc.) on file changes.
CONTROL

# 5. Build.
OUT_FILE="${OUT_DIR}/${PKG_NAME}_${VERSION}_all.deb"
if command -v fpm >/dev/null 2>&1; then
    rm -f "${OUT_FILE}"
    fpm -s dir -t deb -n "${PKG_NAME}" -v "${VERSION}" \
        --architecture all \
        --depends "python3 >= 3.9" --depends python3-venv --depends python3-pip \
        --maintainer "vvindetta <noreply@github.com>" \
        --url "https://github.com/vvindetta/lucy_notes_daemon" \
        --description "Modular notes manager daemon" \
        --after-install "${STAGE_DIR}/DEBIAN/postinst" \
        --before-remove "${STAGE_DIR}/DEBIAN/prerm" \
        --package "${OUT_FILE}" \
        -C "${STAGE_DIR}" \
        opt usr etc lib
else
    dpkg-deb --build --root-owner-group "${STAGE_DIR}" "${OUT_FILE}"
fi

echo "Built ${OUT_FILE}"
