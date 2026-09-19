#!/bin/bash
# Fetch NAR pairs for packages rebuilt (same name+version, new store hash) between two consecutive
# nixos-25.05 channel advances: 813260.6c8f0cca8451 -> 813435.ff06bd3398fb (a mass rebuild).
# Layout: data/rebuild/before/<name>.nar and data/rebuild/after/<name>.nar
# Usage: bash fetch_rebuilds.sh [target_total_bytes] [max_nar_bytes]
set -uo pipefail
cd "$(dirname "$0")/data"
TARGET=${1:-250000000}
MAX=${2:-30000000}
mkdir -p rebuild/before rebuild/after
total=0
while read -r h1 h2 name; do
  [ "$total" -ge "$TARGET" ] && break
  out1="rebuild/before/$name.nar"; out2="rebuild/after/$name.nar"
  if [ -s "$out1" ] && [ -s "$out2" ]; then total=$((total + $(stat -c %s "$out1") + $(stat -c %s "$out2"))); continue; fi
  ok=1
  for pair in "$h1 $out1" "$h2 $out2"; do
    set -- $pair; hash=$1; out=$2
    info=$(curl -sSL "https://cache.nixos.org/$hash.narinfo") || { ok=0; break; }
    url=$(echo "$info" | sed -n 's/^URL: //p'); size=$(echo "$info" | sed -n 's/^NarSize: //p'); comp=$(echo "$info" | sed -n 's/^Compression: //p')
    [ -z "$size" ] && { ok=0; break; }
    if [ "$size" -gt "$MAX" ] || [ "$size" -lt 65536 ]; then ok=0; break; fi
    case "$comp" in xz) dec="xz -d";; zstd) dec="zstd -d";; *) ok=0; break;; esac
    curl -sSL "https://cache.nixos.org/$url" | $dec > "$out.tmp" && mv "$out.tmp" "$out" || { ok=0; break; }
  done
  if [ "$ok" = 1 ]; then
    s=$(( $(stat -c %s "$out1") + $(stat -c %s "$out2") )); total=$((total + s)); echo "got  $name ($s B, total $total)"
  else
    rm -f "$out1" "$out2" "$out1.tmp" "$out2.tmp"
  fi
done < ../rebuild_pairs.txt
du -sh rebuild/*
