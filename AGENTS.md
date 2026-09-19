# Style Guides

## Communication

- Phrase positively or neutrally; avoid negations and "not this, but that" contrasts.
- Frame positively: show the promising, successful aspects of the recommended path. Mention an alternative only when its trade-offs deserve our attention.
- The reader has limited working memory and limited ability to search back through recent text. Include only what the current focus needs.

Sixteen actions that improve writing:
1. Sand off filler words
2. Find the real actors
3. Restore actions to verbs
4. Delete empty verbs
5. Prefer characters as subjects
6. Put subjects and verbs together
7. Put verbs and objects together
8. Make the opening familiar
9. Put new and important information last
10. Repair topic flow
11. Repair stress flow
12. Establish a clear topic sentence
13. Make subjects consistent across a passage
14. Control passive voice deliberately
15. Name responsibility
16. Trim metadiscourse

## Coding: Design By Contract

We document and `assert` every precondition our code relies on (`debug_assert` only on hot paths). Contracts are **expansive** (the caller carries the responsibility), **conceptually simple** (a few sentences of English; simplicity beats familiarity), and **structurally simple** to enforce (few lines, types, data elements, conditionals).

We never write "defensive code" — code that complicates a contract to ease the caller's life. When running code detects that a caller misunderstood the contract, it **fails stop**: panic with a clear message. Stopping is safer than proceeding, and it lets people fix the caller or loosen the contract. Defensive codebases grow buggier over time; DBC codebases stay predictable.

### Caches and hash tables

Caches evict by **random replacement, never LRU**. LRU collapses under specific load patterns, badly enough to cause timeouts or pile load onto other components; RR runs slightly slower under most patterns and equally well under all (an example of the minimax strategy below). The randomness stays uncorrelated with the load even when an attacker controls the inputs, and reproducible for tests and benchmarks; both follow from one construction: **SipHash-1-2(seed, counter)**, secret seed in production, fixed seed in tests. Hash tables whose keys an attacker can influence select buckets with the same keyed SipHash.

## Design

- **Small designs.** Fewer mechanisms and concepts is a major improvement in itself, worth trading efficiency, generality, or asymptotic elegance for. Count mechanisms; merge two that serve one purpose; remove one that another dominates. State assumptions as preconditions instead of adding checking machinery. Prefer designs a reader understands from a handful of concrete numbers and rules (Knuth: design for concrete capacities).
- **Minimax.** Perform acceptably across all data and load patterns rather than excellently on some and badly on others. People adopt "works fine for typical cases, plus this specific advantage" and filter out "great for X but watch out for Y". A pattern for which we achieve nothing is a real cost.
- **Tune performance last**, over many knobs at once.

# Glossary

Use exactly these terms in code, comments, docs, and conversation.

- **Content-Defined Chunking (CDC)** — splitting a byte stream at positions determined by a rolling hash of its content. **Boundary** — such a position. **Chunk** — the bytes between two boundaries.
- **Chunk hash** — BLAKE3 of a chunk's bytes; the chunk's id in the **chunk store** (content-addressed, refcounted; `put`/`get`).
- **Manifest** — a file's ordered list of entries, each a whole-chunk reference or a **slice** `(chunk hash, offset, len)`. Reconstructing a file costs one range read per entry.
- **Ingest** — take in a file: chunk, look up, run the miss handler, store new chunks, write the manifest. **Reconstruct** — the reverse.
- **Miss handler** — for a chunk whose hash misses: compare it (lockstep walk at shift 0 + gram search) against the stored chunks between its neighbours' sources; emit slices for equal runs ≥ `minslice`, store the rest.
- **Bao proof** — BLAKE3 tree nodes proving a byte range belongs to a file; served on request.
- **Rebuild corpus / nars corpus** — the two test data sets under `data/`; see DESIGN.md.

# System-wide Preconditions

- BLAKE3 is collision-free over all data this store will ever hold. Chunk-hash equality *is* byte equality — relied upon, never checked.

# Environment

- `curl` works (`curl -sSL <url> | sed 's/<[^>]*>//g'` reads a page as text). If TLS fails with "certificate is not yet valid", run `hwclock -s` and retry.
- After a VM reboot run `bash scripts/guest-setup.sh` once: fixes the clock, sets git config, installs pypy3 + zstd, re-fetches missing data sets. In each fresh shell, `export GIT_CONFIG_GLOBAL=/tmp/gitconfig` before using git.
- Benchmark: `pypy3 bench.py data/rebuild --systems cdcs,cdc` (or `data/nars`); see `bench.py --help`.
- Reference docs: Xet spec https://www.ietf.org/archive/id/draft-denis-xet-05.html ; Lore design https://epicgames.github.io/lore/explanation/system-design/

# Working Habits

- Run long commands in the foreground and wait for them to finish. Never `sleep`, never set a tool timeout, never poll: a guessed duration is always either too long (wasting the user's time) or too short (interrupting the work).

# Project Files

- `DESIGN.md` — current design, decisions, costs, open questions. Read it before design or coding work.
- `REJECTED.md` — alternatives we considered and set aside, one line each with the reason. Consult it only when someone proposes an alternative; leave it out of routine reading.
