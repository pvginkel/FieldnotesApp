#!/bin/sh
set -eu

CACHE_PREFIX="$1"
LOCK_FILE="$2"
TARGET_PATH="$3"

BUILD_CACHE_URL="${BUILD_CACHE_URL:-http://build-cache.jenkins-prd.svc.cluster.local}"

HASH=$(sha256sum "$LOCK_FILE" | cut -d' ' -f1)
CACHE_FILE="${CACHE_PREFIX}-${HASH}.tgz"

echo "Downloading cache: ${CACHE_FILE}"

mkdir -p "$TARGET_PATH"

curl -sS -f "${BUILD_CACHE_URL}/${CACHE_FILE}" | tar -xzf - -C "$TARGET_PATH"

echo "Cache restored successfully"
