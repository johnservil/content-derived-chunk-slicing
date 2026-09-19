# Content-Derived Chunk Slicing — Design

## Idea in one sentence

Keep classic Content-Defined Chunking exactly as the competitor has it, and add one entry type to
the manifest: a **slice** `(chunk hash, offset, len)` that references a byte range of a stored
chunk. When a chunk misses, compare it against its neighbours' sources and store only the bytes that
differ.

## Requirements

- API: `ingest(bytes) → file id`, `read(file id, range) → bytes`, `delete(file id)`.
- Server can answer `(file id, range) → bytes + Bao proof` (BLAKE3), by storing, regenerating, or
  memoizing tree nodes as it sees fit.
- Chunk ids are BLAKE3 hashes of chunk contents (what the colleague's system already does).

## Components

1. **Chunker.** Gear rolling hash, min/avg/max = avg/4 / avg / min(64 KiB, 8·avg). Unchanged from
   the competitor. Average size is the main open knob (see Results).
2. **Chunk store.** Content-addressed by BLAKE3, one refcount per chunk. Unchanged.
3. **Manifest.** Ordered entries, each either a whole-chunk reference or a slice
   `(chunk hash, offset, len)`. A read issues one `pread` per entry. Contract: `offset + len ≤ chunk length`.
4. **Miss handler** (the new part). For a chunk whose hash misses:
   - Candidate sources = the stored chunks that sit *between* the sources of the nearest hit on
     each side, in the store's sequential order (i.e. the old file's chunks that correspond to this
     gap). With one neighbour hit, the single adjacent stored chunk.
   - Compare the missed chunk against each candidate: lockstep walk at shift 0 (they are aligned by
     construction, since the surrounding boundaries matched) plus an 8-gram LZ77 search for runs at
     other shifts. Equal runs ≥ `minslice` become slices; the rest is stored as new chunk(s).
   - No new index. No extra I/O beyond reading the candidate chunks (bounded by matched bytes).
5. **BLAKE3 subtree dedup** (shared layer, applies equally to competitor and to us): the incoming
   file's tree CVs at 64 KiB and up, indexed by CV → (file, offset), find unchanged aligned regions
   at zero I/O. Credited to neither side in comparisons.

## Results so far (bench.py, PyPy; see AGENTS.md for invocation)

Two real corpora from cache.nixos.org:
- **rebuild** — 20 packages × (before, after) across one mass rebuild of nixos-25.05 (240 MiB).
  19 of 20 pairs are byte-identical in length; changes are 32-char store-path rewrites.
- **nars** — 17 packages × 4 releases 23.11 → 25.05 (1.1 GiB): real version changes.

| corpus | system | ratio | metadata |
|---|---|---|---|
| rebuild | cdc 8 KiB | 0.450 | 0.26% |
| rebuild | **cdc 8 KiB + slices** | **0.436** | 0.27% |
| rebuild | fixed 64 KiB | 0.590 | 0.06% |
| nars | cdc 8 KiB | 0.675 | 0.32% |
| nars | fixed 64 KiB | 0.968 | 0.08% |

Interpretation:
- On rebuild data, 87% of 8 KiB chunks hit exactly; only 13% miss and most misses are isolated.
  CDC at 8 KiB is already within ~10% of the floor there, so slices add 1.4 points. Where the
  miss handler engages it does what was predicted: pangomm's one missed 42 KiB chunk (122 changed
  bytes, one glib store hash) became 280 stored bytes + 2 slices instead of 42 KiB.
- The remaining unrecovered bytes on rebuild are dominated by one file (nvidia-open: a 9 KiB size
  change then a 708-chunk contiguous miss run). Neighbour-based candidates cannot help there; only
  an index that finds shifted content can (see Open questions).
- **The expected payoff of slices is coarser chunks, not a finer ratio at 8 KiB.** The competitor's
  cost of 8 KiB chunks is metadata and reads (colleague's numbers: 55 MiB metadata / 1.5 M refs at
  8 KiB vs 1.6 MiB / 164 k at 256 KiB). Slices make an edit cost ~300 B instead of one whole chunk,
  which should let chunk size rise to 64–256 KiB with little ratio loss. **Not yet measured.**

Colleague's table (his data, algorithms unknown), for orientation:
exact/256KiB 1581 MiB, 1.6 MiB meta · exact/8KiB 1311 MiB, 55 MiB meta ·
exact/1KiB 1551 MiB, 450 MiB meta · slices/1KiB/4 1047 MiB, 3.3 MiB meta.

## Cost measures (keep all four in view)

1. Metadata construction at ingest (hashing, chunking) — per byte, fixed.
2. Metadata storage — cheap in bytes; the *index* must fit RAM, manifests need not.
3. Search cost at ingest — an ingester dial: it may skip the miss handler under load; every setting
   yields a correct manifest.
4. **Read cost** — one `pread` per manifest entry. Slices per 64 KiB is the number to watch;
   `minslice` is its knob (sweep on systemd: 32 → 219/64 KiB, 128 → 28, 256 → 15, 512 → 15,
   1024 → 5; ratio 0.640 → 0.652 → 0.670 → 0.686 → 0.769). **Knee at 256.**

## Open questions, in priority order

1. **Chunk size with slices.** Run `--cdc-avg 65536` (and 262144) for both cdc and cdc+slices on
   both corpora. Hypothesis: cdc's ratio degrades toward fixed-block levels while cdc+slices holds,
   at 8–30× less metadata. This is the chart for the colleague.
2. **Shifted-content finder for long miss runs** (the nvidia case). A second, optional mechanism:
   index content-defined segment hashes → (chunk, offset) so a miss with no hit neighbours can still
   locate its source at any shift. Measure how many bytes it addresses before building it.
3. `minslice` and the entropy-coding of literal residue (deferred with zstd).
4. Compaction of chunks kept alive by small slices.

## Rationale

- **Slices instead of stored deltas.** Shilane 2012, Ddelta, DARE store deltas as deltas, forcing
  recursive reads and chain-depth rules. Literal chunks keep reconstruction to one hop.
- **Neighbour sources instead of a new index.** The chunk hashes the competitor already computes are
  the cheapest exhaustive finder there is: every unchanged ≥ 2·avg run is found at any shift. The
  neighbours of a hit tell us where the miss's content lives; no anchor index is needed for the
  common case.
- **No zstd yet.** Range reads stay plain `pread`s; this layer's ratio is measured alone.

## Prior art

Shilane et al. 2012; Xia et al. Ddelta / DARE (duplicate-adjacency, which the miss handler is);
FastCDC; Hugging Face Xet; Epic Lore; VCDIFF, xdelta, `zstd --patch-from`.
