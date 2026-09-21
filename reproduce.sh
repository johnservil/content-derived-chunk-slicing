#!/bin/bash
# Reproduce every number in report.html from scratch:
#   1. fetch the corpus (corpus.txt pins each NAR by store hash and sha256; ~1.3 GiB from cache.nixos.org)
#   2. run bench.py for cdc and cdc+slices at 8, 64 and 256 KiB average chunk size on both corpora
#   3. render report.html from results.jsonl
# Requires: pypy3 (CPython works, ~10× slower), curl, xz, zstd, sha256sum. ~10 min on PyPy, 16 GB RAM
# for the nars corpus at 8 KiB. Idempotent: present, verified corpus files are kept.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-$(command -v pypy3 || command -v python3)}
SIZES=${SIZES:-"8192 65536 262144"}

bash fetch_corpus.sh

rm -f results.jsonl
for corpus in data/rebuild data/nars; do
  for avg in $SIZES; do
    echo "== $corpus avg=$avg"
    "$PY" bench.py "$corpus" --no-baselines --systems cdcs,cdc --cdc-avg "$avg" --json results.jsonl 2>/dev/null \
      | grep -E '^cdc\+slices:|^ +cdc:'
  done
done

"$PY" render_report.py results.jsonl report.html
