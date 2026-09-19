#!/usr/bin/env pypy3
"""
Measurement tool for Content-Derived Chunk Slicing (see DESIGN.md).

Ingests every file under the given roots in sorted path order and reports, for our design and two
baselines, how many bytes end up as literals versus references, plus metadata volume and search cost.

Nothing is stored; this is a simulator faithful to the ingest pipeline only.

Contracts (asserted, never defended against):
  - Input files are read whole into memory; each must fit.
  - BLOCK is a power of two; H divides BLOCK; MINSLICE >= GRAM.
"""
import sys, os, time, hashlib, struct, argparse
from collections import defaultdict

BLOCK = 65536
GRAM = 8          # match-finder gram size (bytes); also the anchor word width

# --------------------------------------------------------------------------------------------
# Hashing helpers

def block_key(b):
    # Stand-in for SLAKE3: any collision-free content hash serves the measurement.
    return hashlib.blake2b(b, digest_size=16).digest()

def word_at(b, p):
    return struct.unpack_from('<Q', b, p)[0]

# --------------------------------------------------------------------------------------------
# Anchors: strict local maximum of the 8-byte little-endian word within +-h bytes.
# Scalar AE-style scan. Returns list of (offset, word) for a block.

def anchors(b, h):
    n = len(b) - GRAM + 1
    if n <= 0:
        return []
    out = []
    # For each position p we need: word[p] > word[q] for all q in [p-h, p+h], q != p.
    # Sliding maximum via the "asymmetric extremum" walk: keep running max; when it has survived
    # h bytes to the right, check it also beat everything h to the left (guaranteed by construction
    # when the previous anchor/run-max was farther left). We implement the exact definition with a
    # monotonic deque for clarity and correctness at this prototype's scale.
    from collections import deque
    dq = deque()  # positions with decreasing words, window [p-h, p+h]
    words = [word_at(b, p) for p in range(n)]
    # Precompute with deque over window size 2h+1 centered on p.
    W = 2 * h + 1
    for i in range(n):
        w = words[i]
        while dq and words[dq[-1]] <= w:
            dq.pop()
        dq.append(i)
        c = i - h                      # center whose window [c-h, c+h] = [i-2h, i] is now complete
        while dq and dq[0] < i - 2 * h:
            dq.popleft()
        if c >= 0:
            # dq[0] is the max over [c-h, c+h] (clipped at 0). Anchor iff it is at c and strict.
            if dq[0] == c:
                # strictness: no equal word in window
                if len(dq) < 2 or words[dq[1]] != words[c]:
                    out.append((c, words[c]))
    return out

# --------------------------------------------------------------------------------------------
# Match finder: LZ77 over a window of source blocks. Returns list of (src_id, src_off, dst_off, len)
# covering the incoming block greedily, plus literal byte count.

class GramTable:
    """Hash of every GRAM-byte gram of each window block -> list of (src_id, off). Rebuilt per
    incoming block over at most WINDOW blocks; cheap enough for a simulator."""
    def __init__(self, window):
        self.t = {}
        for src_id, data in window:
            n = len(data) - GRAM + 1
            t = self.t
            for p in range(n):
                g = data[p:p + GRAM]
                lst = t.get(g)
                if lst is None:
                    t[g] = [(src_id, p)]
                elif len(lst) < 8:        # cap chain length; keeps degenerate data cheap
                    lst.append((src_id, p))

def find_matches(inc, window, minslice):
    """Greedy LZ77 parse of `inc` against `window` (list of (src_id, bytes)).
    Returns (slices, literal_bytes) where slices = [(src_id, src_off, dst_off, length)]."""
    srcs = dict(window)
    gt = GramTable(window).t
    n = len(inc)
    slices = []
    lit = 0
    p = 0
    while p + GRAM <= n:
        cands = gt.get(inc[p:p + GRAM])
        best = 0; best_src = None; best_off = 0
        if cands:
            for src_id, q in cands:
                s = srcs[src_id]
                # extend forward
                L = GRAM
                maxL = min(n - p, len(s) - q)
                while L < maxL and inc[p + L] == s[q + L]:
                    L += 1
                if L > best:
                    best, best_src, best_off = L, src_id, q
        if best >= minslice:
            # extend backward into preceding literal bytes (greedy parse rarely needs it, cheap win)
            s = srcs[best_src]
            back = 0
            while back < p and best_off - back > 0 and inc[p - back - 1] == s[best_off - back - 1]:
                back += 1
            if back:
                # the last `back` bytes counted as literals are now part of this slice
                lit -= back
                p -= back; best_off -= back; best += back
            slices.append((best_src, best_off, p, best))
            p += best
        else:
            lit += 1
            p += 1
    lit += n - p
    return slices, lit

# --------------------------------------------------------------------------------------------
# Our design

class Sliced:
    name = 'sliced'
    def __init__(self, h, minslice, window_size=4, topk=3):
        self.h, self.minslice, self.window_size, self.topk = h, minslice, window_size, topk
        self.blockfile = open('/workspace/data/bench_blocks.bin', 'w+b'); self.blockfile.truncate(0)
        self.blocks = {}          # block_id -> (file offset, len); bytes live on disk
        self.key_to_id = {}       # block key -> block_id
        self.index = {}           # anchor word -> block_id (single slot, first writer wins; RR would be equivalent for ratio)
        self.next_id = 0
        # stats
        self.in_bytes = 0; self.lit_bytes = 0; self.dup_bytes = 0
        self.n_slices = 0; self.n_blocks = 0; self.n_lookups = 0
        self.n_cands = 0; self.n_false = 0; self.n_index_entries = 0; self.n_inblocks = 0
        self.hist = defaultdict(lambda: [0, 0])   # log2(len) -> [count, bytes]

    def _store(self, data):
        k = block_key(data)
        bid = self.key_to_id.get(k)
        if bid is not None:
            return bid
        bid = self.next_id; self.next_id += 1
        self.blockfile.seek(0, 2); self.blocks[bid] = (self.blockfile.tell(), len(data))
        self.blockfile.write(data); self.key_to_id[k] = bid
        self.n_blocks += 1; self.lit_bytes += len(data)
        for off, w in anchors(data, self.h):
            if w not in self.index:
                self.index[w] = bid
                self.n_index_entries += 1
        return bid

    def _read(self, bid):
        off, ln = self.blocks[bid]
        self.blockfile.seek(off); return self.blockfile.read(ln)

    def ingest(self, data):
        self.in_bytes += len(data)
        prev_src = None
        self.litbuf = bytearray()
        for start in range(0, len(data), BLOCK):
            inc = data[start:start + BLOCK]
            self.n_inblocks += 1
            k = block_key(inc)
            bid = self.key_to_id.get(k)
            self.n_lookups += 1
            if bid is not None:
                self.dup_bytes += len(inc); self.n_slices += 1
                prev_src = bid
                continue
            # vote
            votes = defaultdict(int)
            for off, w in anchors(inc, self.h):
                self.n_lookups += 1
                src = self.index.get(w)
                if src is not None:
                    votes[src] += 1
            cands = sorted(votes, key=lambda s: -votes[s])[:self.topk]
            window_ids = list(cands)
            if prev_src is not None:
                for extra in (prev_src, prev_src + 1):
                    if extra in self.blocks and extra not in window_ids:
                        window_ids.append(extra)
            window_ids = window_ids[:self.window_size]
            self.n_cands += len(window_ids)
            if window_ids:
                window = [(i, self._read(i)) for i in window_ids]
                slices, lit = find_matches(inc, window, self.minslice)
            else:
                slices, lit = [], len(inc)
            for _, _, _, L in slices:
                bucket = L.bit_length() - 1
                self.hist[bucket][0] += 1; self.hist[bucket][1] += L
            used = {s[0] for s in slices}
            self.n_false += len(window_ids) - len(used)
            self.n_slices += len(slices)
            self.dup_bytes += len(inc) - lit
            # literal runs are appended to this file's literal stream, which is cut into BLOCK-size
            # blocks (each literal run = one slice into that stream).
            if lit:
                pos = 0
                for _, _, d, L in sorted(slices, key=lambda t: t[2]):
                    if d > pos:
                        self.litbuf += inc[pos:d]; self.n_slices += 1
                    pos = d + L
                if pos < len(inc):
                    self.litbuf += inc[pos:]; self.n_slices += 1
                while len(self.litbuf) >= BLOCK:
                    self._store(bytes(self.litbuf[:BLOCK])); del self.litbuf[:BLOCK]
            # for adjacency, remember the dominant source of this block
            prev_src = max(votes, key=lambda s: votes[s]) if votes else None
        if self.litbuf:
            self._store(bytes(self.litbuf)); self.litbuf = bytearray()

    def report(self):
        meta = self.n_index_entries * 8 + self.n_slices * 10 + self.n_blocks * 4
        return dict(stored=self.lit_bytes, ratio=self.lit_bytes / self.in_bytes,
                    meta=meta, meta_pct=100 * meta / self.in_bytes,
                    index=self.n_index_entries * 8,
                    slices=self.n_slices, blocks=self.n_blocks,
                    slices_per_64k=self.n_slices / self.n_inblocks,
                    lookups_per_block=self.n_lookups / self.n_inblocks,
                    cands_per_block=self.n_cands / self.n_inblocks,
                    false_per_block=self.n_false / self.n_inblocks)

# --------------------------------------------------------------------------------------------
# Sync design: sparse strong sync points + lockstep walk (see DESIGN.md)

SYNC_KEY_LEN = 1024

def sync_points(b, h):
    """Content-defined sync positions: local-max word within +-h, keeping only those with a full
    SYNC_KEY_LEN bytes after them. Returns [(offset, key)]."""
    out = []
    for off, w in anchors(b, h):
        if off + SYNC_KEY_LEN <= len(b):
            out.append((off, hashlib.blake2b(b[off:off + SYNC_KEY_LEN], digest_size=16).digest()))
    return out

def walk(inc, src, d, minslice):
    """Lockstep compare of inc against src at shift d (src_off = inc_off + d) over the overlap.
    Returns list of (src_off, dst_off, len) for equal runs >= minslice."""
    lo = max(0, -d); hi = min(len(inc), len(src) - d)
    out = []
    i = lo
    while i < hi:
        if inc[i] == src[i + d]:
            j = i + 1
            while j < hi and inc[j] == src[j + d]:
                j += 1
            if j - i >= minslice:
                out.append((i + d, i, j - i))
            i = j
        else:
            i += 1
    return out

def merge_cover(runs_by_src):
    """Given {src_id: [(src_off, dst_off, len)]}, pick a non-overlapping cover of dst greedily by
    length (longest first). Returns [(src_id, src_off, dst_off, len)]."""
    allr = [(L, sid, so, do) for sid, rs in runs_by_src.items() for so, do, L in rs]
    allr.sort(reverse=True)
    taken = []   # (dst_start, dst_end)
    out = []
    for L, sid, so, do in allr:
        a, b = do, do + L
        ok = True
        for ta, tb in taken:
            if a < tb and ta < b:
                ok = False; break
        if ok:
            taken.append((a, b)); out.append((sid, so, do, L))
    return out

class Sync(Sliced):
    name = 'sync'
    def __init__(self, h, minslice, window_size=4, topk=3):
        Sliced.__init__(self, h, minslice, window_size, topk)
        self.index = {}   # sync key -> (block_id, offset). Single slot; first writer (RR-equivalent).
        self.n_walks = 0

    def _store(self, data):
        k = block_key(data)
        bid = self.key_to_id.get(k)
        if bid is not None:
            return bid
        bid = self.next_id; self.next_id += 1
        self.blockfile.seek(0, 2); self.blocks[bid] = (self.blockfile.tell(), len(data))
        self.blockfile.write(data); self.key_to_id[k] = bid
        self.n_blocks += 1; self.lit_bytes += len(data)
        for off, key in sync_points(data, self.h):
            if key not in self.index:
                self.index[key] = (bid, off); self.n_index_entries += 1
        return bid

    def ingest(self, data):
        self.in_bytes += len(data)
        prev = None          # (src_block_id, d) of previous block's dominant source
        self.litbuf = bytearray()
        for start in range(0, len(data), BLOCK):
            inc = data[start:start + BLOCK]
            self.n_inblocks += 1
            k = block_key(inc); self.n_lookups += 1
            bid = self.key_to_id.get(k)
            if bid is not None:
                self.dup_bytes += len(inc); self.n_slices += 1
                prev = (bid, 0)
                continue
            runs = {}
            tried = set()
            def try_src(sid, d):
                if (sid, d) in tried or sid not in self.blocks:
                    return
                tried.add((sid, d)); self.n_walks += 1
                r = walk(inc, self._read(sid), d, self.minslice)
                if r:
                    runs.setdefault(sid, []).extend(r)
            # 1. adjacency: same shift into the successor block
            covered = 0
            if prev is not None:
                try_src(prev[0] + 1, prev[1])
                covered = sum(L for rs in runs.values() for _, _, L in rs)
            # 2. sync lookups if adjacency left a lot uncovered
            if covered < len(inc) * 3 // 4:
                for off, key in sync_points(inc, self.h):
                    self.n_lookups += 1
                    hit = self.index.get(key)
                    if hit is not None:
                        sid, soff = hit
                        try_src(sid, soff - off)
            self.n_cands += len(tried)
            slices = merge_cover(runs)
            self.n_false += len(tried) - len({sl[0] for sl in slices})
            lit = len(inc) - sum(sl[3] for sl in slices)
            for _, _, _, L in slices:
                bkt = L.bit_length() - 1
                self.hist[bkt][0] += 1; self.hist[bkt][1] += L
            self.n_slices += len(slices)
            self.dup_bytes += len(inc) - lit
            if lit:
                pos = 0
                for _, _, d0, L in sorted(slices, key=lambda t: t[2]):
                    if d0 > pos:
                        self.litbuf += inc[pos:d0]; self.n_slices += 1
                    pos = d0 + L
                if pos < len(inc):
                    self.litbuf += inc[pos:]; self.n_slices += 1
                while len(self.litbuf) >= BLOCK:
                    self._store(bytes(self.litbuf[:BLOCK])); del self.litbuf[:BLOCK]
            # dominant source for adjacency: the (sid, d) covering the most bytes
            if slices:
                best = {}
                for sid, so, do, L in slices:
                    best[(sid, so - do)] = best.get((sid, so - do), 0) + L
                prev = max(best, key=best.get)
            else:
                prev = None
        if self.litbuf:
            self._store(bytes(self.litbuf)); self.litbuf = bytearray()

    def report(self):
        r = Sliced.report(self)
        r['meta'] = self.n_index_entries * 10 + self.n_slices * 10 + self.n_blocks * 4
        r['meta_pct'] = 100 * r['meta'] / self.in_bytes
        r['index'] = self.n_index_entries * 10
        r['walks_per_block'] = self.n_walks / self.n_inblocks
        return r

# --------------------------------------------------------------------------------------------
# Hybrid: word-keyed anchors as shift hypotheses; one candidate block; walk + gram search within it.
# No SLAKE3. Blocks get sequential ids. Exact-dup blocks are caught by the anchor path (one read).

class Hybrid(Sliced):
    name = 'hybrid'
    def __init__(self, h, minslice, max_cands=1):
        Sliced.__init__(self, h, minslice, max_cands, max_cands)
        self.index = {}       # anchor word -> (block_id, offset). First writer (RR-equivalent).
        self.n_walks = 0
        self.max_cands = max_cands

    def _store(self, data):
        # Block id = BLAKE3-style content hash (blake2b stand-in). Identical bytes store once.
        k = block_key(data)
        bid = self.key_to_id.get(k)
        if bid is not None:
            return bid
        bid = self.next_id; self.next_id += 1
        self.blockfile.seek(0, 2); self.blocks[bid] = (self.blockfile.tell(), len(data))
        self.blockfile.write(data); self.key_to_id[k] = bid
        self.n_blocks += 1; self.lit_bytes += len(data)
        for off, w in anchors(data, self.h):
            if w not in self.index:
                self.index[w] = (bid, off); self.n_index_entries += 1
        return bid

    def _match_block(self, inc, sid, d):
        """Walk at shift d, plus gram search for runs at other shifts within the same source block."""
        src = self._read(sid)
        runs = walk(inc, src, d, self.minslice)
        gsl, _ = find_matches(inc, [(sid, src)], self.minslice)
        runs += [(so, do, L) for _, so, do, L in gsl]
        return runs

    def ingest(self, data):
        self.in_bytes += len(data)
        prev = None
        self.litbuf = bytearray()
        for start in range(0, len(data), BLOCK):
            inc = data[start:start + BLOCK]
            self.n_inblocks += 1
            self.n_lookups += 1
            bid = self.key_to_id.get(block_key(inc))
            if bid is not None:
                self.dup_bytes += len(inc); self.n_slices += 1
                prev = (bid, 0)
                continue
            runs = {}; tried = set()
            def try_src(sid, d):
                if sid not in self.blocks or any(t[0] == sid for t in tried):
                    return
                tried.add((sid, d)); self.n_walks += 1
                r = self._match_block(inc, sid, d)
                if r:
                    runs.setdefault(sid, []).extend(r)
            # 1. adjacency
            covered = 0
            if prev is not None:
                try_src(prev[0] + 1, prev[1])
                covered = sum(L for rs in runs.values() for _, _, L in rs)
            # 2. anchor hypotheses, most-voted first, until max_cands sources tried
            if covered < len(inc) * 3 // 4:
                votes = defaultdict(int); shift = {}
                for off, w in anchors(inc, self.h):
                    self.n_lookups += 1
                    hit = self.index.get(w)
                    if hit is not None:
                        sid, soff = hit
                        votes[(sid, soff - off)] += 1
                for (sid, d), _ in sorted(votes.items(), key=lambda kv: -kv[1]):
                    if len(tried) >= self.max_cands + (1 if prev is not None else 0):
                        break
                    try_src(sid, d)
            self.n_cands += len(tried)
            slices = merge_cover(runs)
            self.n_false += len(tried) - len({sl[0] for sl in slices})
            lit = len(inc) - sum(sl[3] for sl in slices)
            for _, _, _, L in slices:
                bkt = L.bit_length() - 1
                self.hist[bkt][0] += 1; self.hist[bkt][1] += L
            self.n_slices += len(slices)
            self.dup_bytes += len(inc) - lit
            if lit:
                pos = 0
                for _, _, d0, L in sorted(slices, key=lambda t: t[2]):
                    if d0 > pos:
                        self.litbuf += inc[pos:d0]; self.n_slices += 1
                    pos = d0 + L
                if pos < len(inc):
                    self.litbuf += inc[pos:]; self.n_slices += 1
                while len(self.litbuf) >= BLOCK:
                    self._store(bytes(self.litbuf[:BLOCK])); del self.litbuf[:BLOCK]
            if slices:
                best = {}
                for sid, so, do, L in slices:
                    best[(sid, so - do)] = best.get((sid, so - do), 0) + L
                prev = max(best, key=best.get)
            else:
                prev = None
        if self.litbuf:
            self._store(bytes(self.litbuf)); self.litbuf = bytearray()

    def report(self):
        r = Sliced.report(self)
        r['meta'] = self.n_index_entries * 10 + self.n_slices * 10 + self.n_blocks * 4
        r['meta_pct'] = 100 * r['meta'] / self.in_bytes
        r['index'] = self.n_index_entries * 10
        r['walks_per_block'] = self.n_walks / self.n_inblocks
        return r

# --------------------------------------------------------------------------------------------
# Baseline 1: fixed 64 KiB whole-block dedup

class Fixed:
    name = 'fixed64K'
    def __init__(self):
        self.seen = set(); self.in_bytes = 0; self.lit_bytes = 0; self.n = 0
    def ingest(self, data):
        self.in_bytes += len(data)
        for start in range(0, len(data), BLOCK):
            b = data[start:start + BLOCK]
            k = block_key(b); self.n += 1
            if k not in self.seen:
                self.seen.add(k); self.lit_bytes += len(b)
    def report(self):
        meta = len(self.seen) * 36 + self.n * 16
        return dict(stored=self.lit_bytes, ratio=self.lit_bytes / self.in_bytes,
                    meta=meta, meta_pct=100 * meta / self.in_bytes, chunks=len(self.seen))

# --------------------------------------------------------------------------------------------
# Baseline 2: classic CDC (Gear rolling hash), avg 8 KiB, min 2 KiB, max 64 KiB

_GEAR = [struct.unpack('<Q', hashlib.blake2b(bytes([i]), digest_size=8).digest())[0] for i in range(256)]

class CDC:
    name = 'cdc8K'
    def __init__(self, avg=8192, mn=2048, mx=65536):
        self.mask = avg - 1; self.mn, self.mx = mn, mx
        self.seen = set(); self.in_bytes = 0; self.lit_bytes = 0; self.n = 0
    def ingest(self, data):
        self.in_bytes += len(data)
        n = len(data); start = 0; G = _GEAR; mask = self.mask; mn = self.mn; mx = self.mx
        while start < n:
            h = 0; p = start; end = min(n, start + mx)
            p = min(start + mn, end)
            for q in range(start, p):
                h = ((h << 1) + G[data[q]]) & 0xFFFFFFFFFFFFFFFF
            while p < end:
                h = ((h << 1) + G[data[p]]) & 0xFFFFFFFFFFFFFFFF
                p += 1
                if (h & mask) == 0:
                    break
            chunk = data[start:p]
            k = block_key(chunk); self.n += 1
            if k not in self.seen:
                self.seen.add(k); self.lit_bytes += len(chunk)
            start = p
    def report(self):
        meta = len(self.seen) * 36 + self.n * 16
        return dict(stored=self.lit_bytes, ratio=self.lit_bytes / self.in_bytes,
                    meta=meta, meta_pct=100 * meta / self.in_bytes, chunks=len(self.seen))

# --------------------------------------------------------------------------------------------

def fmt(n):
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024: return f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}TiB'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roots', nargs='+')
    ap.add_argument('--h', type=int, default=4096, help='anchor half-window (bytes)')
    ap.add_argument('--minslice', type=int, default=256)
    ap.add_argument('--window', type=int, default=1)
    ap.add_argument('--only', default='', help='substring filter on file names')
    ap.add_argument('--no-baselines', action='store_true')
    ap.add_argument('--systems', default='hybrid', help='comma list: hybrid,sliced,sync')
    ap.add_argument('--sync-h', type=int, default=8192, help='sync-point half-window (bytes)')
    a = ap.parse_args()
    assert BLOCK & (BLOCK - 1) == 0 and a.minslice >= GRAM

    files = []
    for root in a.roots:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if a.only in fn:
                    files.append(os.path.join(dp, fn))
    files.sort()

    systems = []
    if 'sliced' in a.systems: systems.append(Sliced(a.h, a.minslice, a.window))
    if 'sync' in a.systems: systems.append(Sync(a.sync_h, a.minslice))
    if 'hybrid' in a.systems: systems.append(Hybrid(a.h, a.minslice, a.window))
    if not a.no_baselines:
        systems += [Fixed(), CDC()]

    total = 0; t0 = time.time()
    for f in files:
        data = open(f, 'rb').read()
        total += len(data)
        for s in systems:
            s.ingest(data)
        el = time.time() - t0
        print(f'{fmt(len(data)):>10}  {el:7.1f}s  {os.path.relpath(f, a.roots[0])}', file=sys.stderr)

    print(f'\nfiles={len(files)} input={fmt(total)} h={a.h} sync_h={a.sync_h} minslice={a.minslice} window={a.window}  '
          f'time={time.time()-t0:.0f}s')
    for s in systems:
        r = s.report()
        line = f'{s.name:>9}: stored={fmt(r["stored"]):>9} ratio={r["ratio"]:.4f} meta={fmt(r["meta"]):>9} ({r["meta_pct"]:.3f}%)'
        for k in ('index', 'slices', 'slices_per_64k', 'blocks', 'chunks', 'lookups_per_block', 'cands_per_block', 'walks_per_block', 'false_per_block'):
            if k in r:
                v = r[k]; line += f' {k}={v:.2f}' if isinstance(v, float) else f' {k}={v}'
        print(line)
        if hasattr(s, 'hist') and s.hist:
            tot_b = sum(v[1] for v in s.hist.values()) or 1
            tot_n = sum(v[0] for v in s.hist.values()) or 1
            print('           match-length histogram: [len range] slices% bytes%')
            cum_n = cum_b = 0
            for k in sorted(s.hist):
                n, b = s.hist[k]; cum_n += n; cum_b += b
                print(f'           [{1<<k:>6}..{(1<<(k+1))-1:>6}] {100*n/tot_n:5.1f}% {100*b/tot_b:5.1f}%   cum {100*cum_n/tot_n:5.1f}% {100*cum_b/tot_b:5.1f}%')

if __name__ == '__main__':
    main()
