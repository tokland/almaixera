# Almaixera: single image serving both DEV and PRO.
#
# It bundles MariaDB next to the Django app on purpose: django1/settings.py
# talks to the database over localhost, and the app has no migrations in the
# repo (.gitignore excludes them), so a database can only ever be created by
# restoring a dump. Keeping both in one image means "build once, start
# anywhere" without a second service to wire up.
#
# The database dump is NOT part of this image -- see docker/start.sh.

FROM python:3.7-slim-bullseye

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=django1.settings

# mariadb-server replaces the MySQL 5.7 that production runs; it reads 5.7
# dumps unchanged. default-libmysqlclient-dev + gcc are only needed to compile
# mysqlclient, so they are removed again in the same layer.
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        mariadb-server \
        mariadb-client \
        gzip \
        procps \
    ; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
    ; \
    pip install --no-cache-dir -r /app/requirements.txt; \
    apt-get purge -y --auto-remove build-essential default-libmysqlclient-dev pkg-config; \
    rm -rf /var/lib/apt/lists/*

COPY . /app

# email_settings.py is gitignored, so it is absent from a clean checkout and
# settings.py imports it unconditionally. Ship the sample as a fallback; mount
# the real one over it in production.
RUN set -eux; \
    if [ ! -f /app/django1/email_settings.py ]; then \
        cp /app/django1/email_settings.sample.py /app/django1/email_settings.py; \
    fi; \
    python manage.py collectstatic --noinput

# mysqld needs a writable datadir; docker/start.sh mounts a named volume here
# so the database survives container replacement.
VOLUME /var/lib/mysql

EXPOSE 5000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
