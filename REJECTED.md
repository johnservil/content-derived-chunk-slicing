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
