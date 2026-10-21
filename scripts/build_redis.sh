#!/usr/bin/env bash
# Builds a local Redis 7 server from source into .tooling/redis.
# Used only for local development because this host has no package manager
# or container runtime available. See docs/development.md.
set -euo pipefail

REDIS_VERSION="${REDIS_VERSION:-7.2.5}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLING="$ROOT/.tooling"
PREFIX="$TOOLING/redis"

if [ -x "$PREFIX/bin/redis-server" ]; then
  echo "redis-server already built at $PREFIX/bin/redis-server"
  "$PREFIX/bin/redis-server" --version
  exit 0
fi

mkdir -p "$TOOLING/src"
cd "$TOOLING/src"

TARBALL="redis-${REDIS_VERSION}.tar.gz"
if [ ! -f "$TARBALL" ]; then
  echo "Downloading Redis ${REDIS_VERSION}..."
  curl -fsSL -o "$TARBALL" "https://download.redis.io/releases/${TARBALL}"
fi

rm -rf "redis-${REDIS_VERSION}"
tar xzf "$TARBALL"
cd "redis-${REDIS_VERSION}"

echo "Building Redis ${REDIS_VERSION} (this takes a few minutes)..."
make -j"$(sysctl -n hw.ncpu)" MALLOC=libc >"$TOOLING/redis-build.log" 2>&1

mkdir -p "$PREFIX"
make PREFIX="$PREFIX" install >>"$TOOLING/redis-build.log" 2>&1

echo "Built: $PREFIX/bin/redis-server"
"$PREFIX/bin/redis-server" --version
