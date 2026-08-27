#!/bin/bash
# Starts MariaDB, restores the seed dump the first time round, then runs the
# app either with runserver (dev) or with the same uwsgi invocation production
# has been running for years (prod).
set -euo pipefail

APP_DIR=/app
DATADIR=/var/lib/mysql
SEED=/seed/dump.sql.gz
SEEDED_MARKER="$DATADIR/.almaixera-seeded"

MODE=${MODE:-dev}
PORT=${PORT:-5000}
RELOAD_DB=${RELOAD_DB:-0}
DB_NAME=${DB_NAME:-rostoll}
DB_PASSWORD=${DB_PASSWORD:-5jitk9}

log() { printf '[almaixera] %s\n' "$*"; }

mysql_root() { mysql --protocol=socket -u root "$@"; }

start_database() {
    # /run is a fresh tmpfs in every container, so the socket directory the
    # mariadb package ships is gone by the time we boot.
    install -d -o mysql -g mysql -m 755 /run/mysqld

    if [ ! -d "$DATADIR/mysql" ]; then
        log "initialising empty datadir at $DATADIR"
        mysql_install_db --user=mysql --datadir="$DATADIR" --auth-root-authentication-method=normal >/dev/null
    fi
    chown -R mysql:mysql "$DATADIR"

    log "starting mariadb"
    mysqld --user=mysql --datadir="$DATADIR" --bind-address=127.0.0.1 --skip-name-resolve &
    MYSQLD_PID=$!

    for _ in $(seq 1 60); do
        if ! kill -0 "$MYSQLD_PID" 2>/dev/null; then
            log "ERROR: mariadb exited during startup (see the log above)"
            exit 1
        fi
        if mysqladmin --protocol=socket -u root --silent ping >/dev/null 2>&1 \
           || mysqladmin --protocol=socket -u root -p"$DB_PASSWORD" --silent ping >/dev/null 2>&1; then
            log "mariadb is up"
            return 0
        fi
        sleep 1
    done
    log "ERROR: mariadb did not become ready in 60s"
    exit 1
}

# Fresh datadirs authenticate root over the unix socket with no password;
# django connects with one, so pin it on every boot (idempotent).
configure_database() {
    local sql="ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
               CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8 COLLATE utf8_general_ci;
               FLUSH PRIVILEGES;"
    mysql_root -e "$sql" 2>/dev/null || mysql_root -p"$DB_PASSWORD" -e "$sql"
}

seed_database() {
    if [ ! -f "$SEED" ]; then
        if [ ! -f "$SEEDED_MARKER" ]; then
            log "WARNING: no dump mounted at $SEED and database was never seeded."
            log "         The comandes app has no migrations in the repo, so its"
            log "         tables will be missing. Pass a dump to docker/start.sh."
        fi
        return 0
    fi

    if [ -f "$SEEDED_MARKER" ] && [ "$RELOAD_DB" != "1" ]; then
        log "database already seeded ($(cat "$SEEDED_MARKER")); pass --reload-db to restore again"
        return 0
    fi

    log "restoring $DB_NAME from $SEED (this takes a while)"
    mysql_root -p"$DB_PASSWORD" -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`;
        CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8 COLLATE utf8_general_ci;"
    gzip -dc "$SEED" | mysql_root -p"$DB_PASSWORD" --default-character-set=utf8 "$DB_NAME"
    date -Iseconds > "$SEEDED_MARKER"
    log "restore finished"
}

shutdown_database() {
    log "stopping mariadb"
    mysqladmin --protocol=socket -u root -p"$DB_PASSWORD" shutdown >/dev/null 2>&1 || true
    wait "$MYSQLD_PID" 2>/dev/null || true
}

main() {
    cd "$APP_DIR"

    # A dev bind-mount of the repo shadows the image copy, which has no
    # email_settings.py on a clean checkout.
    if [ ! -f django1/email_settings.py ]; then
        log "django1/email_settings.py missing, falling back to the sample"
        cp django1/email_settings.sample.py django1/email_settings.py
    fi

    start_database
    trap shutdown_database EXIT INT TERM
    configure_database
    seed_database

    if [ "$#" -gt 0 ]; then
        log "running: $*"
        "$@" &
    elif [ "$MODE" = "prod" ]; then
        python manage.py collectstatic --noinput >/dev/null
        log "starting uwsgi on :$PORT"
        uwsgi --chdir="$APP_DIR" \
              --http ":$PORT" \
              --module=django1.wsgi:application \
              --env DJANGO_SETTINGS_MODULE=django1.settings \
              --master \
              --processes=10 \
              --harakiri=60 \
              --max-requests=10000 \
              --check-static "$APP_DIR" \
              --vacuum &
    else
        log "starting runserver on :$PORT"
        python manage.py runserver "0.0.0.0:$PORT" &
    fi

    # Foreground wait rather than exec, so the EXIT trap can stop mysqld
    # cleanly and the datadir volume is never left in crash recovery.
    wait $!
}

main "$@"
