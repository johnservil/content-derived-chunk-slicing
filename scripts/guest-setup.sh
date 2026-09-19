#!/bin/bash
# Recreate every guest-side (outside /workspace) change made during the design sessions.
# Idempotent: safe to run repeatedly. Run as root inside the Gondolin VM:
#   bash /workspace/scripts/guest-setup.sh
set -euo pipefail

echo "== 1. Clock (TLS fails with 'certificate is not yet valid' when the VM clock lags)"
hwclock -s || true
date -u

echo "== 2. Git config (home dir is unwritable in this VM; use /tmp/gitconfig)"
export GIT_CONFIG_GLOBAL=/tmp/gitconfig
touch "$GIT_CONFIG_GLOBAL"
git config --global --get-all safe.directory | grep -qx /workspace || git config --global --add safe.directory /workspace
git config --global user.name  >/dev/null 2>&1 || git config --global user.name  pi
git config --global user.email >/dev/null 2>&1 || git config --global user.email pi@localhost
# Make the setting available to interactive shells too.
grep -q GIT_CONFIG_GLOBAL /etc/profile.d/workspace.sh 2>/dev/null || \
  echo 'export GIT_CONFIG_GLOBAL=/tmp/gitconfig' > /etc/profile.d/workspace.sh

echo "== 3. Debian packages: pypy3 (benchmark runtime), zstd (some NARs are zstd-compressed)"
need=""
for p in pypy3 zstd; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
if [ -n "$need" ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need
fi
pypy3 --version

echo "== 4. Data sets (skipped if already present under /workspace/data)"
cd /workspace
if [ ! -s data/lists/nixos-25.05.txt ]; then
  mkdir -p data/lists
  for ch in nixos-23.11 nixos-24.05 nixos-24.11 nixos-25.05; do
    curl -sSL "https://channels.nixos.org/$ch/store-paths.xz" | xz -d > "data/lists/$ch.txt"
  done
fi
# Version-history corpus: 17 packages x 4 releases (~1.1 GB). Script skips files it already has.
bash fetch_nars.sh
# Rebuild corpus: 20 before/after NAR pairs from one mass rebuild (~240 MB). Also skip-if-present.
bash fetch_rebuilds.sh

echo "== done"
