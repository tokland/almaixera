#!/bin/bash
# Builds the almaixera image. Same image for DEV and PRO -- what differs is
# how docker/start.sh runs it.
#
# Usage: docker/build.sh [extra docker build args...]
#   IMAGE=almaixera TAG=latest docker/build.sh --no-cache
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE=${IMAGE:-almaixera}
TAG=${TAG:-latest}

echo "[almaixera] building ${IMAGE}:${TAG}"
docker build -f docker/Dockerfile -t "${IMAGE}:${TAG}" "$@" .

echo
echo "[almaixera] built ${IMAGE}:${TAG}"
echo "[almaixera] next: docker/start.sh <dump.sql.gz>"
