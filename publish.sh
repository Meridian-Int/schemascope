#!/usr/bin/env bash
#
# publish.sh — bump the version, build schemascope, and upload it to PyPI.
#
#   Usage:
#     ./publish.sh          # auto-bump the patch version (e.g. 0.1.0 -> 0.1.1) and publish
#     ./publish.sh 0.2.0    # publish an explicit version instead of a patch bump
#
# Reads the PyPI API token from .env (PYPI_TOKEN=pypi-...). The .env file is
# git-ignored; this script never prints the token. Each run publishes a NEW
# version — PyPI never allows re-uploading or editing a version that already
# exists, so bumping the version is how an updated README reaches the PyPI page.

set -euo pipefail
cd "$(dirname "$0")"

# --- 1. load credentials from .env ------------------------------------------
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found next to this script." >&2
  exit 1
fi
set -a                      # export every var defined while sourcing
# shellcheck disable=SC1091
source ./.env
set +a

# PyPI wants username "__token__" and the API token as the password.
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="${PYPI_TOKEN:-}"
if [[ -z "${TWINE_PASSWORD}" ]]; then
  echo "ERROR: PYPI_TOKEN is not set in .env (expected: PYPI_TOKEN=pypi-...)." >&2
  exit 1
fi

TARGET="${TWINE_REPOSITORY_URL:-public PyPI (https://upload.pypi.org/legacy/)}"

# --- 2. current + next version (version.py is the single source of truth) ----
VERSION_FILE="src/schemascope/version.py"
CUR_VERSION="$(python3 - "$VERSION_FILE" <<'PY'
import re, sys, pathlib
print(re.search(r'__version__\s*=\s*"([^"]+)"',
                pathlib.Path(sys.argv[1]).read_text()).group(1))
PY
)"
NEW_VERSION="$(python3 - "${1:-}" "${CUR_VERSION}" <<'PY'
import sys
arg, cur = sys.argv[1].strip(), sys.argv[2]
if arg:
    print(arg)
else:
    p = cur.split(".")
    p[-1] = str(int(p[-1]) + 1)     # bump the patch component
    print(".".join(p))
PY
)"

# --- 3. confirm (irreversible) ----------------------------------------------
echo "Current version : ${CUR_VERSION}"
echo "Publishing as   : ${NEW_VERSION}   ->   ${TARGET}"
echo "A published version can never be re-uploaded or edited."
read -r -p "Type 'publish' to continue: " reply
[[ "${reply}" == "publish" ]] || { echo "Aborted (version unchanged)."; exit 1; }

# --- 4. write the new version to the single source of truth ------------------
python3 - "$VERSION_FILE" "${NEW_VERSION}" <<'PY'
import re, sys, pathlib
path, new = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
text, n = re.subn(r'(__version__\s*=\s*")[^"]+(")', rf'\g<1>{new}\g<2>', p.read_text(), count=1)
if n != 1:
    sys.exit(f"ERROR: could not update version in {path}")
p.write_text(text)
print(f"Set version to {new}")
PY

# --- 5. clean build ----------------------------------------------------------
rm -rf dist build src/schemascope.egg-info
python3 -m pip install --quiet --upgrade build twine
python3 -m build

# --- 6. validate + upload ----------------------------------------------------
python3 -m twine check dist/*
python3 -m twine upload dist/*     # honors TWINE_REPOSITORY_URL if set in .env

echo "Done — schemascope ${NEW_VERSION} uploaded to ${TARGET}."
echo "Next: commit the bump ->  git add ${VERSION_FILE} && git commit -m \"Release ${NEW_VERSION}\""
