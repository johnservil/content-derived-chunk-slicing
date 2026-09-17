# Content-Derived Chunk Slicing — Design

## Idea

The store holds fixed 64 KiB **blocks** of literal bytes, content-addressed. Each file has a
**manifest**: an ordered list of **slices** `(block key, offset, len)`. Ingest carries all the
deduplication work; storage and reads stay trivial. Ingest runs LZ77 over a window of a few stored
blocks that a small index selects, so it matches exact duplicates, same-length edits, shifts of any
length, and shared substrings down to ~32 B against the whole corpus.

## Requirements

- API: `ingest(bytes) → file id`, `read(file id, range) → bytes`, `delete(file id)`.
- The server answers `(file id, range) → bytes + Bao proof` (BLAKE3), by storing, regenerating,
  or memoizing tree nodes as it sees fit.
- Precondition (stated, never checked): SLAKE3 is collision-free; key equality is byte equality.

## Components

1. **Block store.** 64 KiB blocks keyed by SLAKE3 hash, one refcount each, grouped into 4–8 MiB
   **extents** for sequential writes and S3 objects. Rationale: 64 KiB clears every current SSD's
   indirection unit, reads in the same latency as 4 KiB, and bounds the bytes a 1-byte slice keeps
   alive.
2. **Manifest.** Ordered slices. A read issues one `pread` per slice. Contract: `offset + len ≤ 64 KiB`.
3. **Anchor index.** One hash table `anchor word → block key`, bucketed by SipHash-1-2 of the word.
   Each stored block contributes its ~8 anchors (*h* = 4 KiB gives spacing ≥ 4 KiB guaranteed,
   ~8 KiB expected). Entries ≈ 8 B ⇒ ≈ 0.1% of data. A per-file key list (≈ 0.01%) lets `delete`
   remove exactly its entries. Full buckets evict by random replacement.
4. **Match finder.** LZ77 over a window of ≤ ~4 blocks: build a 4/8-gram hash table over the window
   (~20 µs per block), search the incoming block (~50–100 µs), emit slices ≥ 32 B and literals. The
   window evicts by random replacement.
5. **Proof service.** Store BLAKE3 parent nodes down to group size *G* (provisionally 1 MiB,
   ≈ 0.003%); per request, rehash the enclosing *G* region with correct chunk counters (≤ 16 block
   reads, ~0.3 ms) to regenerate lower nodes. Memoize in a random-replacement cache.

All randomness comes from SipHash-1-2(seed, counter): secret seed in production, fixed seed in tests.

## Ingest, per 64 KiB of input

One streaming pass computes the block key (SLAKE3), BLAKE3 root and *G*-level nodes, and anchors.
1. Look up the block key. On a hit, emit `(key, 0, 65536)` with zero I/O and stop.
2. Look up the anchors; each hit votes for a block key. Take the top 1–3 as candidates.
3. Form the window: candidates, plus the previous block's source and its successor.
4. Run the match finder; emit slices for matches and literals for the rest. New literal bytes become
   blocks (keyed, anchored, indexed). Write the manifest; bump refcounts.

**Ingester dials** (every setting yields a correct manifest): index only anchors whose word matches a
bit pattern; query a subset of incoming anchors; keep a cold index tier on SSD and consult it only
when idle; cap candidates; skip matching under load.

## Delete

Drop the manifest, decrement referenced blocks' refcounts, and remove the file's anchor entries via
its key list. Blocks die at refcount 0. Compaction of blocks kept alive by small slices ("liveness
holes") comes later.

## Costs per 64 KiB, one core

| Measure | Estimate |
|---|---|
| M1 metadata construction | BLAKE3 15–30 µs + SLAKE3 8–15 µs + anchor scan ~10 µs + match finder 50–100 µs ≈ **100–150 µs** (~0.5 GB/s per core; blocks parallelize) |
| M2 metadata size | anchor index 0.1% + key lists 0.01% + refcounts 0.006% + BLAKE3 nodes 0.003% ≈ **0.12%** |
| M3 search | ~8 lookups + ≤ 3 block reads per block; **0 on an exact duplicate** |

## Rationale

- **Storage layout is independent of match finding.** Chunk boundaries add nothing to a search that
  extends across them. Karp–Rabin (1987) invented the rolling hash for substring search; LBFS (2001)
  cut at its values and thereby tied storage granularity to index density. We untie them.
- **Local-maximum anchors.** Any strict total order on words works; max is the identity order.
  The spacing bounds follow from the definition (two maxima cannot lie within *h* of each other).
  One comparison per byte, no table. A bijective mix (odd multiply) would make density
  distribution-independent — a tuning knob.
- **SLAKE3 for block keys.** BLAKE3's chunk counter makes identical bytes at different offsets hash
  differently; counter 0 removes that, and 4 rounds suffice for an internal hash. BLAKE3 remains for
  client-visible proofs.
- **No zstd yet.** Range reads stay plain `pread`s and this layer's ratio is measured alone. Later:
  entropy-code literal blocks; intra-block LZ77 by adding the incoming block to the window.
- **Slices instead of stored deltas.** Shilane 2012, Ddelta, and DARE store deltas as deltas, which
  forces recursive reads and chain-depth rules. Literal blocks keep reconstruction to one hop.

## Adversarial inputs

An attacker who controls file contents controls which words become anchors, and therefore which
index keys and candidates our ingest sees. The design bounds every consequence:

- **Index size and shape.** Local-max spacing caps anchors at 16 per block, so index growth is
  ≤ 16 entries per 64 KiB ingested regardless of content. Buckets are chosen by keyed SipHash, so
  an attacker cannot steer many keys into one bucket. Repeating one word many times steers many
  *entries* onto one key — random replacement bounds that bucket's size, and the eviction pattern
  stays unpredictable.
- **Ingest CPU and I/O.** Per incoming block the work is bounded by construction: ≤ 16 lookups,
  ≤ 3 candidate reads, one match-finder pass over ≤ 4 window blocks. Poisoning the index with
  many blocks sharing an anchor word can at most make later lookups on that word yield useless
  candidates, costing ≤ 3 wasted 64 KiB reads per block — the same cost as a plain miss plus reads,
  and never a slowdown of another tenant's data.
- **Correctness.** Match finder output is derived by comparing bytes; the index only proposes.
  Wrong candidates degrade ratio for the attacker's own files and nothing else. The SLAKE3
  collision-free precondition covers block keys; BLAKE3 proofs are computed from bytes.
- **Information leakage.** A block-key hit reveals that identical 64 KiB bytes already exist in
  the store (standard CAS side channel). Anchor hits reveal nothing to the client, since candidates
  and slice counts never leave the server. Cross-tenant dedup timing is a deployment decision.

## Prior art

Shilane et al. 2012 (similarity sketches + delta); Xia et al. Ddelta/DARE; FastCDC; Hugging Face
Xet; Epic Lore; Prolly trees (Noms/Dolt), bup hashsplit; MAXP (Bjørner, Blass, Gurevich 2010), AE
(Zhang et al. 2015); minimizers and strobemers (bioinformatics); VCDIFF, xdelta, `zstd --patch-from`.

## Open questions, to be decided by data

- Anchor spacing *h* and the votes-per-candidate threshold (set index size and false-candidate reads).
- Window size and minimum slice length (32 B provisional).
- *G* for BLAKE3 node storage; bijective anchor mix; entropy coding of literals; compaction.
- **Experiment** (awaiting the Nix rebuild data set): measure slice vs. literal bytes on
  (a) rebuild pairs and (b) version-history pairs, sweeping *h* and minimum slice length.
