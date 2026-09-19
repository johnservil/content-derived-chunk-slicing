# Rejected Alternatives

One line each: what, and why we set it aside. Consult when someone proposes an alternative; a
rejection here can be reopened with new evidence.

- **Variable-size chunks cut at rolling-hash boundaries (classic CDC).** Ties storage granularity to
  index density; boundaries add nothing to a match search that extends across them.
- **Whole-file blobs as the storage unit.** One small slice keeps a whole file alive; `delete` breaks.
- **Storing deltas as deltas (Shilane 2012, Ddelta, DARE).** Recursive reads and chain-depth rules;
  literal blocks give one-hop reconstruction.
- **Inlining small literals in the manifest.** A second storage mechanism; a tiny block already works.
  Revisit in tuning.
- **zstd inside blocks, now.** Complicates range reads; hides this layer's ratio. Revisit after this
  layer is measured.
- **BLAKE3 CVs as block keys / stored Bao outboard per block.** Chunk counter makes identical bytes at
  different offsets hash differently; storing full outboards costs 0.1% for nodes we can regenerate.
- **Indexing SLAKE3 tree levels (64/32/16 KiB CVs) for sub-block matching.** Finds only shifts that
  are multiples of the leaf size; the match finder over a window finds any shift and dominates it.
- **Aligned byte-diff and adjacency as separate mechanisms.** Both are special cases of the match
  finder (shift 0; previous source in the window).
- **Fixed-stride Merkle index (64 B leaves).** Breaks on any insert whose length is not a multiple of
  the stride.
- **Prolly trees.** Scale-free elegance at the cost of a concept we would rather do without.
- **Suffix array / FM-index over the corpus.** Exhaustive matches, but 1–20× data in index size and
  hard to update incrementally at scale.
- **Rolling-hash (Gear) anchors.** Works; local-maximum anchors do the same job with one comparison
  per byte, no table, and spacing bounds that follow from the definition.
- **Second, counter-0 BLAKE3 per block.** Would only catch shifts that are multiples of 64 KiB.
- **Trusting SLAKE3 sub-block hits without a source read.** Moot once tree levels were dropped.
- **LRU for any cache.** Collapses under specific load patterns; see AGENTS.md.
- **Vector hash-prefilter to pick anchor candidates, then scalar max test.** Prefilter alone is
  alignment-free, but density control on its survivors reintroduces grid segments or the max test.
  The van Herk sliding max computes the exact definition directly at similar cost.

## Rejected 2026-09-19 (measured)

- **Fixed 64 KiB blocks as the storage unit, with a separate finder.** The separation itself is
  sound, but every finder we built was sparser than CDC's own chunk hashes and failed all-or-nothing
  per block. Result: ratio 0.56 vs cdc 0.45 on rebuild data. CDC chunk hashes *are* the cheapest
  exhaustive finder; keep them.
- **MAXP / local-maximum anchor words as index keys.** On machine code the max 8-byte word is
  usually an address or an ASCII store-path hash — exactly the bytes a rebuild rewrites. 16% of
  blocks had every anchor on changed bytes.
- **SLAKE3 (counter-0, 4-round BLAKE3) as block key.** Needed only because of fixed blocks; gone
  with them. Block/chunk ids are plain BLAKE3, as the colleague already has.
- **Sync design (1 KiB strong keys at anchor positions + lockstep walk).** Cheapest read path
  (2.3 slices/64 KiB) but ratio 0.89 vs 0.65: keys landed on volatile bytes and found only long runs.
- **Hybrid (anchor shift hypotheses + walk + gram search on one candidate).** Best of the fixed-block
  family (0.71 on systemd, 0.56 on rebuild) and still behind cdc 8 KiB. Its walk + gram search
  survives as the miss handler's comparison step.
- **Voting over anchor hits; multi-slot index entries; IDF-style popular-key suppression.** Wrinkles
  on a mechanism that was replaced.
