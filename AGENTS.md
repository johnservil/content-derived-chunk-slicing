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

- **Block** — fixed 64 KiB unit of literal storage; unit of refcount/GC. Content-addressed by its **block key**, the SLAKE3 hash of its bytes.
- **Slice** — a manifest entry `(block key, offset, len)`: a byte range of a stored block.
- **Manifest** — a file's ordered list of slices. Reconstructing a file costs one range read per slice.
- **Ingest** — take in a file: find matches, store new literal bytes as blocks, write the manifest. **Reconstruct** — the reverse.
- **SLAKE3** — BLAKE3 with chunk counter fixed to 0 and 4 rounds. Internal content hash for block keys; never exposed.
- **Bao proof** — BLAKE3 tree nodes proving a byte range belongs to a file; served on request.
- **Anchor** — a position whose 8-byte word is the strict maximum within *h* bytes on either side (MAXP/AE local-maximum sampling). Content-defined, hash-free. The word is the index key.
- **Candidate** — a stored block that anchor votes select as a likely match source. **Window** — the small set of candidate blocks the match finder searches.
- **Match finder** — LZ77-style search of an incoming block against the window; emits slices for matches ≥ ~32 B, literals otherwise.

# System-wide Preconditions

- SLAKE3 is collision-free over all data this store will ever hold. Key equality *is* byte equality — relied upon, never checked.

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
