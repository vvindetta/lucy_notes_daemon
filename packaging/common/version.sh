#!/usr/bin/env bash
# Print the project version read from pyproject.toml.
# Usage: packaging/common/version.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export ROOT_DIR

python3 - <<'PY'
import os, sys
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

with open(os.path.join(os.environ["ROOT_DIR"], "pyproject.toml"), "rb") as f:
    data = tomllib.load(f)
sys.stdout.write(data["project"]["version"])
PY
