#!/bin/bash
# Deploys the working copy to the production host and restarts it there.
#
# Usage: docker/deploy-pro.sh [options] [HOST]
#
#   HOST                  ssh target (default: zaudera)
#   -d, --dir PATH        remote checkout (default: ~/src/almaixera)
#   -n, --dry-run         show what would be sent and stop; changes nothing
#       --delete          also remove remote files that are gone locally
#       --with-settings   sync django1/settings.py and email_settings.py too
#                         (excluded by default: they are gitignored, so the
#                         host keeps its own)
#       --restart POLICY  restart policy for the container
#                         (default: unless-stopped, so a reboot brings the
#                         site and its cron back)
#       --env-file PATH   settings to install on the host, including the sync
#                         password (default: docker/pro.env when it exists;
#                         see docker/pro.env.sample)
#       --no-env-file     do not copy any settings file
#       --sync-password P one-off override of the file's SYNC_PASSWORD
#       --sync-user U     user for that sync (default: admin)
#       --sync-schedule C cron expression (default: Wednesday 01:00 Madrid)
#       --sync-args "..." extra flags for sync.sh, e.g. --dry-run
#       --no-build        skip the image build, just restart the container
#       --prune           docker image prune -f on the host afterwards
#   -h, --help            this message
#
# The database is NEVER touched: no dump is passed to docker/start.sh, so the
# almaixera-prod-db volume is reused as-is. Restoring a dump over production
# stays a manual, deliberate act -- this script has no flag for it.
#
# Before rebuilding, the image currently in use is tagged almaixera:previous,
# so a bad deploy can be rolled back without another build.
#
# Secrets travel in docker/pro.env, copied to the host as mode 600 and read
# there by docker/start.sh. They are never typed on the command line, never
# committed, and never baked into an image.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR=$PWD

HOST=zaudera
REMOTE_DIR='~/src/almaixera'
DRY_RUN=0
DELETE=0
WITH_SETTINGS=0
RESTART=
BUILD=1
PRUNE=0
RESTART_SET=0
ENV_FILE=
NO_ENV_FILE=0
SYNC_USER=
SYNC_PASSWORD=
SYNC_SCHEDULE=
SYNC_ARGS=

die() { echo "error: $*" >&2; exit 1; }
say() { printf '[deploy] %s\n' "$*"; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        -d|--dir)         REMOTE_DIR=${2:?}; shift 2 ;;
        -n|--dry-run)     DRY_RUN=1; shift ;;
        --delete)         DELETE=1; shift ;;
        --with-settings)  WITH_SETTINGS=1; shift ;;
        --restart)        RESTART=${2:?}; RESTART_SET=1; shift 2 ;;
        --sync-user)      SYNC_USER=${2:?}; shift 2 ;;
        --sync-password)  SYNC_PASSWORD=${2:?}; shift 2 ;;
        --sync-schedule)  SYNC_SCHEDULE=${2:?}; shift 2 ;;
        --sync-args)      SYNC_ARGS=${2:?}; shift 2 ;;
        --env-file)       ENV_FILE=${2:?}; shift 2 ;;
        --no-env-file)    NO_ENV_FILE=1; shift ;;
        --no-build)       BUILD=0; shift ;;
        --prune)          PRUNE=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        -*)               die "unknown option: $1" ;;
        *)                HOST=$1; shift ;;
    esac
done

# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
say "checking $HOST"
ssh -o ConnectTimeout=15 "$HOST" true \
    || die "cannot ssh to $HOST"

remote() { ssh "$HOST" "$@"; }

remote "command -v docker >/dev/null" \
    || die "docker not found on $HOST"
remote "docker ps >/dev/null 2>&1" \
    || die "$HOST cannot run docker as this user (group 'docker'?)"
remote "test -f ${REMOTE_DIR}/docker/start.sh" \
    || die "${REMOTE_DIR} on $HOST does not look like an almaixera checkout"

if [ "$NO_ENV_FILE" = "1" ]; then
    ENV_FILE=
elif [ -n "$ENV_FILE" ]; then
    # An explicit --env-file that does not exist is a typo, not a decision.
    [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE"
elif [ -f "${REPO_DIR}/docker/pro.env" ]; then
    ENV_FILE=${REPO_DIR}/docker/pro.env
fi

# An explicit --restart wins; otherwise the env file decides; otherwise default
# to surviving a reboot, which is what a server wants and what plain
# docker/start.sh does not do on its own.
if [ "$RESTART_SET" = "0" ]; then
    if [ -n "$ENV_FILE" ] && grep -qE '^[[:space:]]*RESTART=' "$ENV_FILE"; then
        RESTART=
    else
        RESTART=unless-stopped
    fi
fi

# --------------------------------------------------------------------------- #
# Send the code
# --------------------------------------------------------------------------- #
# Mirrors .dockerignore, plus the host's own state: its git checkout, its
# database dumps and the big local fixtures that were never part of the app.
rsync_args=(-az --info=stats1 --human-readable
    --exclude '.git/'
    --exclude '__pycache__/' --exclude '*.py[cod]'
    --exclude '.mypy_cache/'
    --exclude 'static/'
    --exclude '*.sql.gz' --exclude '*.sql'
    --exclude 'db.sqlite3'
    --exclude 'env/' --exclude 'virtualenv*' --exclude '.venv/'
    --exclude '*.code-workspace'
    --exclude 'almaixera*.json' --exclude 'natureco/'
    --exclude 'providers/*/products.json'
    --exclude 'providers/*/skipped.csv'
    --exclude 'docker/pro.env')

# settings.py and email_settings.py are gitignored, so the host's copies are
# the authoritative ones. Overwriting them from a dev machine is how a laptop
# quietly reconfigures production.
if [ "$WITH_SETTINGS" = "0" ]; then
    rsync_args+=(--exclude 'django1/settings.py' --exclude 'django1/email_settings.py')
else
    say "WARNING: sending django1/settings.py and email_settings.py to $HOST"
fi

[ "$DELETE" = "1" ] && rsync_args+=(--delete)

if [ "$DRY_RUN" = "1" ]; then
    say "dry run: what would be sent to ${HOST}:${REMOTE_DIR}"
    rsync "${rsync_args[@]}" --dry-run --itemize-changes "${REPO_DIR}/" "${HOST}:${REMOTE_DIR}/"
    if [ -n "$ENV_FILE" ]; then
        say "dry run: would install $ENV_FILE as ${REMOTE_DIR}/docker/pro.env (mode 600)"
        say "dry run: it sets $(grep -oE '^[A-Za-z_]+=' "$ENV_FILE" | tr -d '=' | tr '\n' ' ')"
    else
        say "dry run: no settings file would be copied"
    fi
    say "dry run: nothing was changed"
    exit 0
fi

say "syncing code to ${HOST}:${REMOTE_DIR}"
rsync "${rsync_args[@]}" "${REPO_DIR}/" "${HOST}:${REMOTE_DIR}/"

if [ -n "$ENV_FILE" ]; then
    say "installing $(basename "$ENV_FILE") on the host (mode 600)"
    # Created 600 before any content is written, so the secret is never
    # briefly world-readable on the host.
    remote "install -m 600 /dev/null ${REMOTE_DIR}/docker/pro.env && cat > ${REMOTE_DIR}/docker/pro.env" < "$ENV_FILE"
fi

# --------------------------------------------------------------------------- #
# Build, keeping a rollback point
# --------------------------------------------------------------------------- #
if [ "$BUILD" = "1" ]; then
    # Tagging costs nothing (same layers) and leaves a named image to fall
    # back to; without it the previous build is only a dangling id.
    if remote "docker image inspect almaixera:latest >/dev/null 2>&1"; then
        say "tagging the running image as almaixera:previous"
        remote "docker tag almaixera:latest almaixera:previous"
    fi
    say "building on $HOST (this takes a few minutes)"
    remote "cd ${REMOTE_DIR} && docker/build.sh" 
fi

# --------------------------------------------------------------------------- #
# Restart -- no dump argument, so the database volume is reused untouched
# --------------------------------------------------------------------------- #
start_cmd="cd ${REMOTE_DIR} && docker/start.sh --mode prod"
if [ -n "$RESTART" ]; then
    start_cmd+=" --restart $(printf '%q' "$RESTART")"
fi
for var in SYNC_USER SYNC_PASSWORD SYNC_SCHEDULE SYNC_ARGS; do
    value=${!var}
    if [ -n "$value" ]; then
        flag="--${var,,}"          # SYNC_PASSWORD -> --sync_password
        flag=${flag//_/-}          #               -> --sync-password
        start_cmd+=" ${flag} $(printf '%q' "$value")"
    fi
done

say "restarting almaixera-prod (database volume kept)"
remote "$start_cmd"

# --------------------------------------------------------------------------- #
# Did it actually come up?
# --------------------------------------------------------------------------- #
say "waiting for the app to answer"
if remote 'for i in $(seq 1 60); do
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5000/ 2>/dev/null || true)
        case "$code" in 2*|3*) echo "  HTTP $code"; exit 0 ;; esac
        sleep 5
    done
    exit 1'; then
    say "deploy OK"
else
    echo >&2
    echo "[deploy] FAILED: the app did not answer on :5000" >&2
    echo "[deploy] logs:     ssh $HOST docker logs --tail 50 almaixera-prod" >&2
    echo "[deploy] rollback: ssh $HOST 'docker tag almaixera:previous almaixera:latest && cd ${REMOTE_DIR} && docker/start.sh --mode prod'" >&2
    exit 1
fi

if [ "$PRUNE" = "1" ]; then
    say "pruning dangling images on $HOST"
    remote "docker image prune -f" | tail -2
fi

remote "docker ps --filter name=almaixera-prod --format '  {{.Names}}  {{.Status}}  {{.Ports}}'"
if [ -n "$SYNC_PASSWORD" ]; then
    remote "docker exec almaixera-prod cat /etc/cron.d/almaixera-ecoplaza 2>/dev/null | tail -1" \
        | sed 's/^/  cron: /'
fi
say "disk on $HOST: $(remote 'df -h / | tail -1')"
