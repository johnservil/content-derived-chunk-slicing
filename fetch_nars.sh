#!/bin/bash
# Fetch NARs for a set of packages across nixos channel releases into data/nars/<channel>/<name>.nar
# Usage: ./fetch_nars.sh [max_nar_bytes]
# Requires: data/lists/<channel>.txt (from channels.nixos.org/<channel>/store-paths.xz)
set -euo pipefail
cd "$(dirname "$0")/data"
MAX=${1:-80000000}
CHANNELS="nixos-23.11 nixos-24.05 nixos-24.11 nixos-25.05"
PKGS="hello coreutils python3-3 openssl-3 curl-8 git-2 gnutar sqlite-3 zlib-1 glibc-2 perl-5 vim-9 nginx-1 libxml2-2 ncurses-6 systemd-25 gnugrep gawk"

for ch in $CHANNELS; do
  mkdir -p "nars/$ch"
  for pkg in $PKGS; do
    path=$(grep -E "^/nix/store/[a-z0-9]{32}-${pkg}[0-9.]*(-[0-9p.]+)?$" "lists/$ch.txt" | head -1 || true)
    [ -z "$path" ] && continue
    hash=${path#/nix/store/}; hash=${hash%%-*}
    name=${path#/nix/store/*-}
    out="nars/$ch/$name.nar"
    [ -s "$out" ] && { echo "have  $ch $name"; continue; }
    info=$(curl -sSL "https://cache.nixos.org/$hash.narinfo")
    url=$(echo "$info" | sed -n 's/^URL: //p')
    size=$(echo "$info" | sed -n 's/^NarSize: //p')
    comp=$(echo "$info" | sed -n 's/^Compression: //p')
    if [ "$size" -gt "$MAX" ]; then echo "skip  $ch $name ($size B)"; continue; fi
    case "$comp" in
      xz)   dec="xz -d" ;;
      zstd) dec="zstd -d" ;;
      *)    echo "skip  $ch $name (compression $comp)"; continue ;;
    esac
    curl -sSL "https://cache.nixos.org/$url" | $dec > "$out.tmp" && mv "$out.tmp" "$out"
    echo "got   $ch $name ($size B)"
  done
done
du -sh nars/*
