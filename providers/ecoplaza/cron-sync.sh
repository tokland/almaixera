#!/bin/bash
# What the weekly cron job inside the almaixera container actually runs.
#
# Cron gives a job an almost empty environment, so the credentials are read
# from /etc/almaixera-sync.env (written by docker/entrypoint.sh from the
# container's own environment) rather than inherited or put on the command
# line.
#
# Output goes to a log file and, so `docker logs` shows it too, to PID 1's
# stdout. A lock file keeps a slow run from overlapping the next one.
set -uo pipefail

ENV_FILE=${SYNC_ENV_FILE:-/etc/almaixera-sync.env}
LOG_DIR=${SYNC_LOG_DIR:-/var/log/almaixera}
LOG=$LOG_DIR/ecoplaza-sync.log
LOCK=/var/lock/almaixera-ecoplaza-sync.lock

mkdir -p "$LOG_DIR" "$(dirname "$LOCK")"

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

: "${ALMAIXERA_URL:=http://127.0.0.1:5000}"
: "${ALMAIXERA_USER:=admin}"
: "${OUTPUT:=/var/lib/almaixera/providers/ecoplaza/products.json}"
export ALMAIXERA_URL ALMAIXERA_USER ALMAIXERA_PASSWORD OUTPUT

run() {
    echo "=============================================================="
    echo "[ecoplaza-sync] starting $(date -Iseconds)"
    if [ -z "${ALMAIXERA_PASSWORD:-}" ]; then
        echo "[ecoplaza-sync] ERROR: no ALMAIXERA_PASSWORD in $ENV_FILE, aborting"
        return 1
    fi
    /app/providers/ecoplaza/sync.sh ${SYNC_ARGS:-} 2>&1
    local status=$?
    echo "[ecoplaza-sync] finished $(date -Iseconds) with status $status"
    return $status
}

# flock -n: if last week's run is somehow still going, skip rather than pile up.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[ecoplaza-sync] another run is still in progress, skipping" >>"$LOG"
    exit 0
fi

if [ -w /proc/1/fd/1 ]; then
    run 2>&1 | tee -a "$LOG" > /proc/1/fd/1
    exit "${PIPESTATUS[0]}"
fi
run >>"$LOG" 2>&1
