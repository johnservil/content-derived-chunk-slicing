# Content-Derived Chunk Slicing

A CDC store with one extra manifest entry type. Chunks grow from 8 KiB to 64 KiB and the
compression ratio improves.

Throughout, **compression ratio** means bytes the store holds divided by bytes ingested, over a
whole corpus. 0.50 means the store holds half of what it took in; lower is better.

## Idea

The store keeps its Gear chunker, BLAKE3 chunk ids, content-addressed chunk store and per-file
manifests. The manifest gains a second entry type, the **slice** `(chunk hash, offset, len)`: a byte
range of a stored chunk. Reading a file still costs one range read per entry.

When an incoming chunk misses the store, the ingester compares it against the stored chunks its
neighbours point at. The chunk before the miss hit stored chunk *s*, so the old version of the
missed chunk is most likely *s+1*. The comparison is a byte walk at shift 0 plus an 8-byte-gram
search for runs at other shifts. Equal runs of 256 bytes or more become slices; the bytes between
them become new chunks. Once a miss resolves against stored chunk *s*, the next miss tries *s+1*
first, so a run of edited chunks walks the old file alongside the new one.

## Results

Two corpora of NixOS package archives (NAR files) from cache.nixos.org, pinned in `corpus.txt`:

- **rebuild** — 20 packages, each before and after one nixos-25.05 mass rebuild (240 MiB). Same
  source, same version; the binaries differ in embedded store-path hashes.
- **releases** — 17 packages across four NixOS releases, 23.11 to 25.05 (1.0 GiB). Real version
  upgrades.

Each row ingests one corpus file by file with a plain CDC store (`cdc`) and with the same store plus
slices (`cdc + slices`).

| corpus   | avg chunk | cdc   | cdc + slices | entries / 64 KiB |
|----------|-----------|-------|--------------|------------------|
| rebuild  | 8 KiB     | 0.450 | **0.430**    | 8.0 → 6.0        |
| rebuild  | 64 KiB    | 0.519 | **0.472**    | 1.0 → 1.4        |
| rebuild  | 256 KiB   | 0.600 | **0.546**    | 0.2 → 0.7        |
| releases | 8 KiB     | 0.675 | **0.603**    | 8.0 → 8.9        |
| releases | 64 KiB    | 0.764 | **0.644**    | 1.0 → 3.7        |
| releases | 256 KiB   | 0.823 | **0.684**    | 0.2 → 3.0        |

`cdc` and `cdc + slices` columns: compression ratio. `entries / 64 KiB`: manifest entries per
64 KiB of file, plain CDC → with slices; one range read per entry when reading the file back.

At 64 KiB with slices, the releases corpus compresses to 0.644, below plain CDC at 8 KiB (0.675),
with a quarter of the manifest entries.

## Reproduce

`./reproduce.sh` fetches the corpus, verifies each archive by sha256, runs every cell above and
renders `report.html`. It needs pypy3, curl, xz and zstd, about ten minutes, and 16 GB of RAM.

`bench.py` is the algorithm: `CDC` is the plain store, `CDCSlices` adds the miss handler.
`DESIGN.md` records the design and `REJECTED.md` the alternatives we measured and set aside.
