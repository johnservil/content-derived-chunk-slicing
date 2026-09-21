# Content-Derived Chunk Slicing

A one-step addition to a content-defined-chunking (CDC) store that lets the average chunk grow from
8 KiB to 64 KiB while the storage ratio *improves*. Measured on 1.3 GiB of real NixOS package
archives; reproducible with `./reproduce.sh`.

## The problem

A CDC store deduplicates at chunk granularity. A chunk either hits the store (its BLAKE3 id exists)
or misses, and a miss stores the whole chunk. A one-byte edit therefore costs one full chunk, so
the average chunk size is a dial between two costs:

- **Small chunks (8 KiB):** edits are cheap, but every file costs 8 manifest entries per 64 KiB,
  and reconstructing a range means one read per entry. Metadata and read fan-out dominate.
- **Large chunks (256 KiB):** metadata shrinks 30×, but a small edit now stores 256 KiB.

On our version-history corpus, moving from 8 KiB to 256 KiB chunks raises the stored fraction from
0.675 to 0.823. Every CDC store sits somewhere on this dial.

## The idea

Keep everything the CDC store already has — Gear chunker, BLAKE3 chunk ids, content-addressed
refcounted chunk store, per-file manifests — and add **one manifest entry type**:

    slice = (chunk hash, offset, len)      # a byte range of a stored chunk

A manifest is an ordered list of entries, each either a whole-chunk reference or a slice.
Reconstructing a file costs one range read per entry, exactly as before. Slices are literal
references into chunks that already exist; there are no deltas, no chains, no second storage
format, and `delete` keeps working through the existing refcounts.

Slices are produced by a **miss handler**. When an incoming chunk's hash misses:

1. **Find candidates without an index.** The chunks around the miss usually hit, and each hit tells
   us where its bytes live: stored chunk *s*. If the chunk before the miss resolved to *s*, the old
   version of the missed chunk is almost certainly *s+1*, the chunk stored right after it. With a
   hit on the far side too, the candidates are the stored chunks that lie *between* the two
   neighbours' sources. This is the whole candidate search: the chunk hashes the store already
   computes locate the source of a miss at any shift, for free.

2. **Carry the source forward.** After a miss resolves — by slices against stored chunk *s* — the
   next miss tries *s+1* first. Runs of consecutive misses, the normal case at 64 KiB where most
   chunks contain some edit, walk the old file in lockstep with the new one. Without this step,
   coarse chunks rarely have a hit neighbour and the handler has nothing to compare against.

3. **Compare.** Read the candidate chunks (bounded: a handful of chunks, no more than the matched
   bytes). Run a lockstep byte walk at shift 0 (the two chunks are aligned by construction, since
   the surrounding boundaries matched) plus an 8-byte-gram LZ77 search for equal runs at other
   shifts. Equal runs of at least `minslice` = 256 B become slices; each remaining run of differing
   bytes is stored as a new chunk and referenced whole.

That is the entire mechanism. The chunker, hashes, store and manifest format are untouched apart
from the new entry type. An ingester under load may skip the miss handler for any chunk and store it
whole; every setting yields a correct manifest.

### Why this works

- **Boundaries are content-defined**, so an unchanged region of the new file produces the same
  chunks as the old file, at any shift. Hits are therefore reliable position markers into old
  content, and the misses between them are the edited regions — already localised to within a
  chunk, with their old counterparts identified.
- **Edits are small relative to chunks.** In a rebuild, a binary changes by a few 32-byte store
  paths; in a version upgrade, functions move and constants change but most bytes survive at some
  shift. At 64 KiB, a rescued miss yields a median of 3 slices (rebuild) or 6 (nars), covering a
  median 99.6% and 94% of its bytes; the rest is stored as new chunks.
- **No new index.** Candidate discovery reuses the chunk-hash lookups the store performs anyway.
  The only added I/O is reading the candidate chunks, which the handler is about to reference.

## Results

Two corpora from cache.nixos.org, listed in `corpus.txt` with store hashes and sha256 checksums:

- **rebuild** — 20 packages before and after one nixos-25.05 mass rebuild (240 MiB). Same
  sources, same versions; only embedded store-path hashes change.
- **nars** — 17 packages across four releases, 23.11 → 25.05 (1.0 GiB). Real version upgrades.

Ratio = stored bytes ÷ ingested bytes over the whole corpus (lower is better). Metadata counts
36 B per stored chunk, 16 B per whole-chunk reference, 20 B per slice.

| corpus  | avg chunk | cdc   | cdc + slices | entries / 64 KiB | metadata        |
|---------|-----------|-------|--------------|------------------|-----------------|
| rebuild | 8 KiB     | 0.450 | **0.430**    | 8.0 → 6.0        | 0.26% → 0.28%   |
| rebuild | 64 KiB    | 0.519 | **0.472**    | 1.0 → 1.4        | 0.03% → 0.06%   |
| rebuild | 256 KiB   | 0.600 | **0.546**    | 0.2 → 0.7        | 0.01% → 0.02%   |
| nars    | 8 KiB     | 0.675 | **0.603**    | 8.0 → 8.9        | 0.32% → 0.45%   |
| nars    | 64 KiB    | 0.764 | **0.644**    | 1.0 → 3.7        | 0.04% → 0.16%   |
| nars    | 256 KiB   | 0.823 | **0.684**    | 0.2 → 3.0        | 0.01% → 0.13%   |

What the table says:

- Slices improve every cell by 2–14 points, more as chunks grow.
- **cdc + slices at 64 KiB beats plain cdc at 8 KiB** on the version-history corpus (0.644 vs
  0.675) and is within two points on the rebuild corpus, with a quarter of the metadata and 1.4–3.7
  manifest entries per 64 KiB instead of 8.
- Going from 8 KiB to 256 KiB costs plain cdc 15 points on both corpora; with slices the cost is
  8–12 points, and 256 KiB remains a reasonable choice when metadata matters most.
- Where the handler engages on the rebuild corpus, it stores the true byte difference: `edb` stores
  3.7 KB for 3,704 changed bytes; `pangomm` stores 280 B for 122. The residue at 64 KiB is one
  package whose payload is xz-compressed kernel modules, which nothing can match.

**Recommendation: 64 KiB average chunks with slices.**

## Contracts and costs

- **Manifest entry:** `offset + len ≤ length of the referenced chunk`. A read is one `pread` per
  entry; no entry references another entry.
- **Refcounts:** a slice holds a reference on its chunk exactly like a whole-chunk reference does.
  Compaction of chunks kept alive by small slices is future work, and the existing GC stays correct
  without it.
- **BLAKE3 collision freedom is a precondition:** chunk-hash equality *is* byte equality. We rely on
  it and never check it.
- **Ingest cost:** per miss, read ≤ 8 candidate chunks and compare (linear walk + gram table over
  the candidates). Under load the ingester may skip this for any chunk.
- **Read cost:** the number to watch is entries per 64 KiB. `minslice` is its knob; sweeping it on
  one 54 MiB binary gave 219 entries/64 KiB at 32 B, 28 at 128 B, 15 at 256 B, 15 at 512 B; the knee
  is 256 B.

## Files

- `bench.py` — the algorithm as a readable simulator (PyPy). `class CDC` is the plain store,
  `class CDCSlices` is the same store with the miss handler. `walk`, `find_matches` and
  `merge_cover` are the comparison step. The benchmark uses blake2b as a stand-in for BLAKE3; only
  hash equality matters here.
- `reproduce.sh` — fetches and verifies the corpus, runs all six cells, renders `report.html`
  (inline SVG, no external assets). `SIZES="65536" ./reproduce.sh` runs one chunk size.
- `corpus.txt`, `fetch_corpus.sh` — pinned corpus: path, nix store hash, NarSize, sha256.
- `DESIGN.md` — design record with open questions; `REJECTED.md` — alternatives measured and set
  aside, one line each; `STATUS.md` — working notes.

## Prior art

Shilane et al. 2012 (stream-informed delta compression); Xia et al. Ddelta / DARE
(duplicate-adjacency, which the candidate search is); FastCDC; Hugging Face Xet; Epic Lore;
VCDIFF, xdelta, `zstd --patch-from`. The difference from delta-based systems: slices point at
literal bytes in existing chunks, so reconstruction stays one hop and needs no chain-depth policy.
