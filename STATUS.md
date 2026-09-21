# STATUS — read this first when resuming

Last updated: 2026-09-21 (VM now has 16 GB; nars 8 KiB run completed).

## Where we are

The proposal has converged to one sentence: **keep classic CDC (the colleague's design: Gear chunker,
BLAKE3 chunk ids, content-addressed chunk store) and add a slice entry type to the manifest; on a
chunk miss, compare the chunk against the stored chunks that the neighbouring hits point at and store
only the bytes that differ.** DESIGN.md has the full design; REJECTED.md lists everything we tried
and abandoned (fixed 64 KiB blocks, MAXP anchors, SLAKE3, sync keys, hybrid).

The implementation of that proposal lives in `bench.py` as class `CDCSlices` (system name `cdcs`),
with the plain competitor as class `CDC` (system name `cdc`). Everything is measured on two real
corpora under `data/` (not in git; re-fetchable).

## Measured results (results.jsonl has the raw records)

Rebuild corpus (20 before/after NAR pairs, one nixos-25.05 mass rebuild, 240 MiB):

| avg chunk | cdc | cdc+slices | entries/64 KiB (cdc → +slices) | metadata |
|---|---|---|---|---|
| 8 KiB   | 0.450 | **0.430** | 8.0 → 6.0 | 0.26% → 0.28% |
| 64 KiB  | 0.519 | **0.472** | 1.0 → 1.4 | 0.03% → 0.06% |
| 256 KiB | 0.600 | **0.546** | 0.2 → 0.7 | 0.01% → 0.02% |

Of the 10.4 MiB stored for the 20 "after" files at 64 KiB, 9.3 MiB is one pair (nvidia-open): five
`.ko.xz` kernel modules, i.e. **xz-compressed content, unmatchable by anyone** (0.4% of bytes agree
at any shift). Excluding it, cdc+slices at 64 KiB stores 1.1 MiB for 19 files totalling 100 MiB —
every same-length pair is within rounding of its true byte diff (edb: 3.7 KB stored for 3,704 changed
bytes; pangomm: 280 B for 122).

Nars corpus (17 packages × 4 nixos releases, 1.0 GiB, real version changes):

| avg chunk | cdc | cdc+slices | entries/64 KiB (cdc → +slices) | metadata |
|---|---|---|---|---|
| 8 KiB   | 0.675 | **0.603** | 8.0 → 8.9 | 0.32% → 0.45% |
| 64 KiB  | 0.764 | **0.644** | 1.0 → 3.7 | 0.04% → 0.16% |
| 256 KiB | 0.823 | **0.684** | 0.2 → 3.0 | 0.01% → 0.13% |

Sweep complete 2026-09-21; all six rows come from the same code (CV candidates removed). Nars runs
take ~135 s on the 16 GB VM. Answer to DESIGN.md open question 1: **cdc+slices at 64 KiB (0.644)
beats plain cdc at 8 KiB (0.675) on nars, and lands within 2 points on rebuild (0.472 vs 0.450) with
1.4 entries/64 KiB instead of 8 and a quarter of the metadata.** Going from 8 KiB to 256 KiB costs
plain cdc 15 points on both corpora; cdc+slices loses 8 (nars) and 12 (rebuild). Recommendation:
**64 KiB**; the step to 256 KiB gives up 4–7 points for a further 2–3× metadata saving.

## What made the difference (so we do not re-learn it)

1. The miss handler is 100% effective *when it has a candidate*. The candidate problem was solved by
   **carrying the source forward across misses**: after a chunk is resolved by slices against stored
   chunk *s*, the next miss tries *s+1* first (variable `last_source`). Without this, at 64 KiB most
   misses had no hit neighbour and stored the whole chunk.
2. BLAKE3 aligned-region CVs as candidates rescued 0–5% of misses on rebuild and 0% on nars —
   irrelevant once (1) exists. **Removed 2026-09-21** (see REJECTED.md); rebuild 64 KiB moved
   0.470 → 0.472.
3. `minslice` knee is 256 B (sweep on systemd: 32→219 entries/64 KiB, 128→28, 256→15, 512→15, 1024→5).
4. On rebuild data 81% of aligned 64 KiB regions are byte-identical at the same offset; 87% of 8 KiB
   chunks hit exactly. CDC at 8 KiB is already near the floor there; the payoff of slices is being
   able to use coarse chunks (5× fewer manifest entries, 3–4× less metadata) at *better* ratio.

## Ideas noted, not yet built

- **Shift-then-hash-CVs** (user's idea, end of session): once a shift *d* is known from any match,
  the stored file's aligned 64 KiB regions correspond to unaligned windows of the new file at
  `region_start − d`; hashing those windows and looking up the CVs confirms 64 KiB of match per
  32-byte compare with zero I/O. A cheap way to extend a shift hypothesis across a whole file.
- **Shift-aware carry:** `last_source` currently implies shift 0 into *s+1*; carrying the shift *d*
  found by the gram search would handle a length change mid-file. nvidia-open turned out to be xz,
  so no test case for this exists yet in the corpora.
- Chunk size 64 KiB is the likely recommendation; confirm with nars 8 KiB vs 64 KiB numbers.

## Deliverable in progress: reproducible report for the colleague

Target: `git clone <url> && ./reproduce.sh` → deterministic download of the corpora (pinned channel
revisions; `rebuild_pairs.txt` is committed; `fetch_nars.sh` pins package versions; each NAR should
be verified against its `.narinfo` NarHash — not yet implemented), run the sweep to `results.jsonl`,
render a self-contained `report.html` (inline SVG, no external assets). Not yet written. PyPy is the
runtime; the Python is the readable specification; a Rust port is the end state for his codebase.

## Practical

- After VM restart: `bash scripts/guest-setup.sh` (clock, git config, pypy3, zstd, data). Then
  `export GIT_CONFIG_GLOBAL=/tmp/gitconfig` in each shell.
- `bench.py --help`. Systems: `cdcs`, `cdc`, plus the retired `hybrid`, `sliced`,
  `sync`, and baseline `fixed64K` (via omitting `--no-baselines`). `--cdc-avg`, `--minslice`,
  `--only <substring>`, `--json <file>`.
- Runs take 20–40 s on rebuild, 2–3 min on nars. Memory: gram tables for many small chunks need
  > 1 GB on the nars corpus at 8 KiB.
- The benchmark uses blake2b as a stand-in for BLAKE3 (only equality matters).
