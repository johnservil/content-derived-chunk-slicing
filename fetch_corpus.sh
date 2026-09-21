#!/bin/bash
# Fetch the benchmark corpus listed in corpus.txt from cache.nixos.org into data/.
# Each line of corpus.txt: <relative path> <nix store hash> <NarSize> <sha256 of the NAR>.
# A file already present with the right sha256 is kept; every downloaded file is verified.
# Requires: curl, xz, zstd, sha256sum.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data
fail=0
while read -r path hash size sha; do
  case "$path" in '#'*|'') continue;; esac
  out="data/$path"
  if [ -s "$out" ] && [ "$(sha256sum "$out" | cut -d' ' -f1)" = "$sha" ]; then
    echo "have  $path"; continue
  fi
  mkdir -p "$(dirname "$out")"
  info=$(curl -sSL "https://cache.nixos.org/$hash.narinfo")
  url=$(echo "$info" | sed -n 's/^URL: //p')
  comp=$(echo "$info" | sed -n 's/^Compression: //p')
  case "$comp" in xz) dec="xz -d";; zstd) dec="zstd -d -q";; *) echo "FAIL  $path: compression '$comp'"; fail=1; continue;; esac
  curl -sSL "https://cache.nixos.org/$url" | $dec > "$out.tmp"
  got=$(sha256sum "$out.tmp" | cut -d' ' -f1)
  if [ "$got" = "$sha" ]; then
    mv "$out.tmp" "$out"; echo "got   $path ($size B)"
  else
    rm -f "$out.tmp"; echo "FAIL  $path: sha256 $got, expected $sha"; fail=1
  fi
done < corpus.txt
[ "$fail" = 0 ] || { echo "some files failed verification"; exit 1; }
echo "corpus complete: $(grep -vc '^#' corpus.txt) files"
