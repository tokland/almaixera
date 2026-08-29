#!/usr/bin/env bash
#
# Full migration: extract every product from the ecoplaza store and upsert
# them into an almaixera instance (provider "Ecoplaza").
#
# Usage:
#   ./sync.sh [ALMAIXERA_URL] [USER:PASS] [extra upload args...]
#
# Examples:
#   ./sync.sh http://localhost:5000 admin:enricus314
#   ./sync.sh http://localhost:5000 admin:enricus314 --dry-run
#   ./sync.sh https://almaixera.cat admin:secret --skipped-out skipped.csv
#
# Both positional arguments may be omitted and given as environment variables
# instead -- which is what the weekly cron job inside the docker container
# does, so the password never shows up in the process list:
#
#   ALMAIXERA_URL=http://127.0.0.1:5000 \
#   ALMAIXERA_USER=admin ALMAIXERA_PASSWORD=secret ./sync.sh --dry-run
#
# Anything that is not the URL / USER:PASS pair is forwarded verbatim to
# upload_to_almaixera.py (e.g. --limit, --dry-run, --provider-id, --skipped-out).
#
# OUTPUT overrides where the intermediate products.json is written; it defaults
# to this directory, which is read-only in some deployments.
#
set -euo pipefail

usage() {
    cat >&2 <<EOF
Usage: $0 [ALMAIXERA_URL] [USER:PASS] [extra upload args...]
  e.g. $0 http://localhost:5000 admin:enricus314
       $0 http://localhost:5000 admin:enricus314 --dry-run --skipped-out skipped.csv
  URL and credentials may come from ALMAIXERA_URL / ALMAIXERA_USER /
  ALMAIXERA_PASSWORD instead.
EOF
    exit 2
}

# A leading non-option argument is the URL, the next one the credentials; both
# fall back to the environment so cron can keep the password out of argv.
URL=${ALMAIXERA_URL:-}
CREDS=
if [ $# -ge 1 ] && [ "${1#-}" = "$1" ]; then
    URL="$1"; shift
    if [ $# -ge 1 ] && [ "${1#-}" = "$1" ]; then
        CREDS="$1"; shift
    fi
fi

if [ -n "$CREDS" ]; then
    USER="${CREDS%%:*}"
    PASS="${CREDS#*:}"
    if [ "$USER" = "$CREDS" ]; then
        echo "Error: credentials must be USER:PASS" >&2
        usage
    fi
else
    USER=${ALMAIXERA_USER:-}
    PASS=${ALMAIXERA_PASSWORD:-}
fi

if [ -z "$URL" ] || [ -z "$USER" ] || [ -z "$PASS" ]; then
    echo "Error: missing almaixera URL, user or password" >&2
    usage
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JSON="${OUTPUT:-$DIR/products.json}"

# These scripts need 3.9+, and plain "python3" is not always it: inside the
# almaixera container /usr/local/bin/python3 is the 3.7 Django runs on, which
# shadows the 3.9 apt installed at /usr/bin/python3. Take the first candidate
# that is new enough, unless PYTHON says otherwise.
find_python() {
    local candidate
    for candidate in "$@"; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

if [ -n "${PYTHON:-}" ]; then
    PY=$PYTHON
elif ! PY=$(find_python python3 /usr/bin/python3 python3.13 python3.12 python3.11 python3.10 python3.9); then
    echo "Error: no python3 >= 3.9 found (set PYTHON to point at one)" >&2
    exit 1
fi

mkdir -p "$(dirname "$JSON")"

echo ">> [1/2] Extracting products from ecoplaza -> $JSON" >&2
"$PY" "$DIR/scraper.py" --output "$JSON"

echo ">> [2/2] Uploading to almaixera ($URL)" >&2
# Password via env so it does not appear in the process list.
ALMAIXERA_PASSWORD="$PASS" "$PY" "$DIR/upload_to_almaixera.py" "$JSON" \
    --base-url "$URL" --user "$USER" "$@"

echo ">> Done." >&2
