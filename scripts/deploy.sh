#!/usr/bin/env bash
#
# Rebuild-free redeploy of the collector container. Assumes the image has
# already been built (the CI workflow builds it; for a manual deploy pass
# --build or run `docker build -f collector/Dockerfile -t eta-collector .`
# first).
#
# Everything is driven by env vars so the same script works from the CI
# runner and from a plain shell:
#
#   IMAGE          image name              (default: eta-collector)
#   TAG            image tag to run        (default: latest)
#   CONTAINER      container name          (default: eta-collector)
#   ETA_REPO_DIR   canonical clone that holds .env + persistent data
#                                          (default: $HOME/ubi-crib-traffic)
#   ETA_ENV_FILE   path to the .env passed to the container
#                                          (default: $ETA_REPO_DIR/.env)
#   ETA_DATA_DIR   host dir for the persistent SQLite file
#                                          (default: $ETA_REPO_DIR/data)
#
# Usage:
#   scripts/deploy.sh              # recreate container from $IMAGE:$TAG
#   scripts/deploy.sh --build      # build the image first, then recreate
#   TAG=abc1234 scripts/deploy.sh  # pin a specific build

set -euo pipefail

IMAGE="${IMAGE:-eta-collector}"
TAG="${TAG:-latest}"
CONTAINER="${CONTAINER:-eta-collector}"
ETA_REPO_DIR="${ETA_REPO_DIR:-$HOME/ubi-crib-traffic}"
ETA_ENV_FILE="${ETA_ENV_FILE:-$ETA_REPO_DIR/.env}"
ETA_DATA_DIR="${ETA_DATA_DIR:-$ETA_REPO_DIR/data}"

# Resolve the repo root of *this* script, so --build uses the checkout
# the workflow ran from rather than $ETA_REPO_DIR.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_CONTEXT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "${1:-}" = "--build" ]; then
  echo ">> building $IMAGE:$TAG from $BUILD_CONTEXT"
  docker build -f "$BUILD_CONTEXT/collector/Dockerfile" \
    -t "$IMAGE:$TAG" -t "$IMAGE:latest" "$BUILD_CONTEXT"
fi

if [ ! -f "$ETA_ENV_FILE" ]; then
  echo "!! env file not found: $ETA_ENV_FILE" >&2
  echo "   create it from .env.example in your canonical clone." >&2
  exit 1
fi

if ! docker image inspect "$IMAGE:$TAG" >/dev/null 2>&1; then
  echo "!! image $IMAGE:$TAG not found locally; build it first (--build)" >&2
  exit 1
fi

# Decide whether we need to bind-mount a persistent DB dir. Only relative
# SQLite URLs (the default) lose data on container recreation; an absolute
# SQLite path or a Postgres/MySQL URL is left exactly as .env specifies.
DB_URL="$(grep -E '^DATABASE_URL=' "$ETA_ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
DB_URL="${DB_URL%\"}"; DB_URL="${DB_URL#\"}"   # strip optional quotes

MOUNT_ARGS=()
ENV_OVERRIDE=()
case "$DB_URL" in
  ""|sqlite:///./*|sqlite:///[!/]*)
    echo ">> relative SQLite DB detected ('$DB_URL') -> persisting at $ETA_DATA_DIR/eta.db"
    mkdir -p "$ETA_DATA_DIR"
    MOUNT_ARGS=(-v "$ETA_DATA_DIR:/data")
    ENV_OVERRIDE=(-e "DATABASE_URL=sqlite:////data/eta.db")
    ;;
  *)
    echo ">> DATABASE_URL ('$DB_URL') left as-is; no DB volume mounted"
    ;;
esac

echo ">> recreating container $CONTAINER from $IMAGE:$TAG"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --env-file "$ETA_ENV_FILE" \
  "${ENV_OVERRIDE[@]}" \
  "${MOUNT_ARGS[@]}" \
  "$IMAGE:$TAG"

echo ">> done. follow logs with:  docker logs -f $CONTAINER"
