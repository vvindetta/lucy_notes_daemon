#!/usr/bin/env bash
# Build a Fedora/RHEL .rpm package for lucy-notes-daemon.
# Requires: rpmbuild, python3, python3-build.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT_DIR}/packaging/build"
RPM_TOPDIR="${BUILD_DIR}/rpm"

VERSION="$(bash "${ROOT_DIR}/packaging/common/version.sh")"
PKG_NAME="lucy-notes-daemon"

rm -rf "${RPM_TOPDIR}"
mkdir -p "${RPM_TOPDIR}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# 1. Build wheel.
cd "${ROOT_DIR}"
python3 -m build --wheel --outdir "${BUILD_DIR}/wheel"
WHEEL="$(ls -1 "${BUILD_DIR}/wheel"/*.whl | head -n1)"
cp "${WHEEL}" "${RPM_TOPDIR}/SOURCES/"
cp "${ROOT_DIR}/config.txt" "${RPM_TOPDIR}/SOURCES/config.txt"
cp "${ROOT_DIR}/packaging/common/lucy-notes-daemon.service" \
    "${RPM_TOPDIR}/SOURCES/lucy-notes-daemon.service"

# 2. Spec.
SPEC="${RPM_TOPDIR}/SPECS/${PKG_NAME}.spec"
WHEEL_NAME="$(basename "${WHEEL}")"
cat > "${SPEC}" <<SPEC
Name:           ${PKG_NAME}
Version:        ${VERSION}
Release:        1%{?dist}
Summary:        Modular notes manager daemon

License:        GPL-3.0-or-later
URL:            https://github.com/vvindetta/lucy_notes_daemon
Source0:        ${WHEEL_NAME}
Source1:        config.txt
Source2:        lucy-notes-daemon.service

BuildArch:      noarch
Requires:       python3 >= 3.9
Requires:       python3-pip

%description
Lucy daemon watches your notes directories and runs modular processors
(banner, renamer, todo-formatter, git sync, etc.) on file changes.

%prep
# nothing to unpack

%build
# nothing to build; the wheel is pre-built upstream

%install
install -d %{buildroot}/opt/lucy-notes-daemon
install -d %{buildroot}/usr/bin
install -d %{buildroot}/etc/lucy-notes-daemon
install -d %{buildroot}/lib/systemd/user

install -m 0644 %{SOURCE0} %{buildroot}/opt/lucy-notes-daemon/${WHEEL_NAME}
install -m 0644 %{SOURCE1} %{buildroot}/etc/lucy-notes-daemon/config.txt
install -m 0644 %{SOURCE2} %{buildroot}/lib/systemd/user/lucy-notes-daemon.service

cat > %{buildroot}/usr/bin/lucy-notes-daemon <<'LAUNCHER'
#!/usr/bin/env bash
exec /opt/lucy-notes-daemon/venv/bin/lucy-notes-daemon "\$@"
LAUNCHER
chmod 0755 %{buildroot}/usr/bin/lucy-notes-daemon

%post
VENV=/opt/lucy-notes-daemon/venv
if [ ! -x "\${VENV}/bin/python" ]; then
    python3 -m venv "\${VENV}"
fi
"\${VENV}/bin/pip" install --upgrade pip >/dev/null
"\${VENV}/bin/pip" install --upgrade /opt/lucy-notes-daemon/${WHEEL_NAME}

%preun
if [ "\$1" = 0 ]; then
    rm -rf /opt/lucy-notes-daemon/venv || true
fi

%files
/usr/bin/lucy-notes-daemon
/opt/lucy-notes-daemon/${WHEEL_NAME}
/lib/systemd/user/lucy-notes-daemon.service
%config(noreplace) /etc/lucy-notes-daemon/config.txt

%changelog
* Tue Apr 22 2026 vvindetta <noreply@github.com> - ${VERSION}-1
- Automated build for ${VERSION}
SPEC

# 3. Build.
rpmbuild --define "_topdir ${RPM_TOPDIR}" -bb "${SPEC}"

echo "RPMs produced under ${RPM_TOPDIR}/RPMS/"
find "${RPM_TOPDIR}/RPMS" -name '*.rpm'
