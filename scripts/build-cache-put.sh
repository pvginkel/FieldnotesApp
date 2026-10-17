#!/bin/sh
set -eu

CACHE_PREFIX="$1"
LOCK_FILE="$2"
SOURCE_PATH="$3"

BUILD_CACHE_URL="${BUILD_CACHE_URL:-http://build-cache.jenkins-prd.svc.cluster.local}"

HASH=$(sha256sum "$LOCK_FILE" | cut -d' ' -f1)
CACHE_FILE="${CACHE_PREFIX}-${HASH}.tgz"

echo "Uploading cache: ${CACHE_FILE}"

tar -czf - -C "$SOURCE_PATH" . | curl -sS -f -T - "${BUILD_CACHE_URL}/${CACHE_FILE}"

echo "Cache uploaded successfully"
