# Packaging

This directory contains scripts and specs to build installable packages
of `lucy-notes-daemon` for major Linux distributions.

All scripts read the project version from `pyproject.toml`.

| Target        | Script                     | Output                              |
|---------------|----------------------------|-------------------------------------|
| Debian/Ubuntu | `scripts/build-deb.sh`     | `packaging/build/*.deb`             |
| Fedora/RHEL   | `scripts/build-rpm.sh`     | `packaging/build/rpm/RPMS/**/*.rpm` |
| Arch Linux    | `scripts/build-arch.sh`    | `packaging/build/arch/*.pkg.tar.*`  |

Each script:

1. Builds the Python sdist/wheel with `python -m build`.
2. Assembles a distro-native package that installs the wheel into a system
   `/opt` virtualenv-free location (via `pip install`) and registers a
   `lucy-notes-daemon` launcher in `/usr/bin`.

The packaging workflows in CI (`.github/workflows/packaging.yml`) use these
same scripts so the build is reproducible.

## Local requirements

| Target | Tools                                                      |
|--------|------------------------------------------------------------|
| deb    | `fpm` (`gem install fpm`) or `dpkg-deb`, `python3-build`   |
| rpm    | `rpmbuild`, `python3-build`                                |
| arch   | `makepkg` (Arch Linux host or container), `python3-build`  |

The scripts pick `fpm` if available; otherwise, they fall back to native
tooling where applicable.
