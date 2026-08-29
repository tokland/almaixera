#!/bin/bash
# Starts an almaixera container.
#
# Usage: docker/start.sh [options] [DUMP.sql.gz]
#
#   -m, --mode dev|prod   dev runs runserver with DEBUG and bind-mounts the
#                         working copy for live reload; prod runs uwsgi off the
#                         code baked into the image (default: dev)
#   -p, --port PORT       host port to publish (default: 5000)
#   -n, --name NAME       container name (default: almaixera-<mode>)
#   -r, --reload-db       restore the dump even if this container's volume was
#                         already seeded (destroys the current database)
#   -f, --foreground      stay attached instead of detaching
#       --sync-password P almaixera password for the weekly provider sync;
#                         without it no sync is scheduled (env: SYNC_PASSWORD)
#       --sync-user U     almaixera user for that sync (default: admin)
#       --sync-schedule C cron expression for it (default: "0 1 * * 3",
#                         Wednesday at 01:00 Europe/Madrid)
#       --sync-args "..." extra flags for providers/ecoplaza/sync.sh,
#                         e.g. --dry-run
#   -h, --help            this message
#
# The dump is a START-time argument, never a build-time one: it holds real
# member data, images get pushed and shared, and DEV and PRO want different
# dumps from the same image. It is restored into a named volume the first time
# the container starts, so restarts keep whatever the database has become.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR=$PWD

IMAGE=${IMAGE:-almaixera}
TAG=${TAG:-latest}
MODE=dev
PORT=5000
NAME=
RELOAD_DB=0
DETACH=1
DUMP=

# Sync credentials never live in the repo or the image: they are passed in
# here and end up as container environment (see docker/entrypoint.sh).
SYNC_USER=${SYNC_USER:-}
SYNC_PASSWORD=${SYNC_PASSWORD:-}
SYNC_SCHEDULE=${SYNC_SCHEDULE:-}
SYNC_ARGS=${SYNC_ARGS:-}

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        -m|--mode)       MODE=${2:?}; shift 2 ;;
        -p|--port)       PORT=${2:?}; shift 2 ;;
        -n|--name)       NAME=${2:?}; shift 2 ;;
        -r|--reload-db)  RELOAD_DB=1; shift ;;
        -f|--foreground) DETACH=0; shift ;;
        --sync-user)     SYNC_USER=${2:?}; shift 2 ;;
        --sync-password) SYNC_PASSWORD=${2:?}; shift 2 ;;
        --sync-schedule) SYNC_SCHEDULE=${2:?}; shift 2 ;;
        --sync-args)     SYNC_ARGS=${2:?}; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        -*)              die "unknown option: $1" ;;
        *)               [ -z "$DUMP" ] || die "only one dump can be given"; DUMP=$1; shift ;;
    esac
done

case "$MODE" in
    dev|prod) ;;
    *) die "mode must be dev or prod, got '$MODE'" ;;
esac

NAME=${NAME:-almaixera-$MODE}
VOLUME="${NAME}-db"

docker image inspect "${IMAGE}:${TAG}" >/dev/null 2>&1 \
    || die "image ${IMAGE}:${TAG} not found -- run docker/build.sh first"

args=(run --name "$NAME" -p "${PORT}:5000"
      -v "${VOLUME}:/var/lib/mysql"
      -e "MODE=$MODE" -e "PORT=5000" -e "RELOAD_DB=$RELOAD_DB")

if [ "$MODE" = "dev" ]; then
    args+=(-e DJANGO_DEBUG=1 -v "${REPO_DIR}:/app")
fi

# Only forward what was actually given; the entrypoint has the defaults, and
# an empty SYNC_PASSWORD is what keeps the sync switched off.
for var in SYNC_USER SYNC_PASSWORD SYNC_SCHEDULE SYNC_ARGS; do
    if [ -n "${!var}" ]; then
        args+=(-e "${var}=${!var}")
    fi
done

# The real SMTP credentials live outside the image; mount them when present so
# prod sends mail and dev falls back to the sample.
if [ -f "${REPO_DIR}/django1/email_settings.py" ] && [ "$MODE" = "prod" ]; then
    args+=(-v "${REPO_DIR}/django1/email_settings.py:/app/django1/email_settings.py:ro")
fi

if [ -n "$DUMP" ]; then
    [ -f "$DUMP" ] || die "dump not found: $DUMP"
    case "$DUMP" in
        *.sql.gz|*.gz) ;;
        *) die "dump must be gzipped sql (.sql.gz), got: $DUMP" ;;
    esac
    args+=(-v "$(readlink -f "$DUMP"):/seed/dump.sql.gz:ro")
fi

if docker container inspect "$NAME" >/dev/null 2>&1; then
    echo "[almaixera] removing previous container $NAME (the $VOLUME volume is kept)"
    docker rm -f "$NAME" >/dev/null
fi

if [ "$DETACH" = "1" ]; then
    args+=(-d)
else
    args+=(--rm -it)
fi

echo "[almaixera] starting $NAME (mode=$MODE, port=$PORT, dump=${DUMP:-none})"
docker "${args[@]}" "${IMAGE}:${TAG}"

if [ "$DETACH" = "1" ]; then
    echo
    echo "[almaixera] http://localhost:${PORT}/"
    echo "[almaixera] logs:  docker logs -f $NAME"
    echo "[almaixera] shell: docker exec -it $NAME bash"
    echo "[almaixera] stop:  docker stop $NAME"
fi
