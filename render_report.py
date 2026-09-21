#!/usr/bin/env pypy3
"""Render results.jsonl (one record per bench.py run) into a self-contained report.html.

Contract: every record holds systems 'cdc' and 'cdc+slices'; one record per (corpus, cdc_avg).
"""
import json, sys, html

CORPUS_LABEL = {'data/rebuild': 'rebuild', 'data/nars': 'releases'}
CORPUS_DESC = {
    'rebuild': '20 packages, before and after one nixos-25.05 mass rebuild (240 MiB). Same sources, '
               'same versions; only store-path hashes inside the binaries change.',
    'releases': '17 packages across four NixOS releases 23.11 → 25.05 (1.0 GiB). Real version upgrades.',
}
KIB = 1024


def load(path):
    recs = {}
    for line in open(path):
        r = json.loads(line)
        c = CORPUS_LABEL[r['roots'][0]]
        recs[(c, r['cdc_avg'])] = r
    return recs


def svg_chart(title, series, ylabel, ymin, ymax, fmt):
    """series: list of (label, colour, [(x_label, y)]) with identical x labels. Returns SVG text."""
    W, H, L, R, T, B = 420, 260, 56, 16, 28, 40
    xs = [x for x, _ in series[0][2]]
    n = len(xs)
    def px(i): return L + (W - L - R) * (i + 0.5) / n
    def py(y): return T + (H - T - B) * (1 - (y - ymin) / (ymax - ymin))
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="sans-serif" font-size="11">']
    out.append(f'<text x="{W/2}" y="16" text-anchor="middle" font-size="13" font-weight="bold">{html.escape(title)}</text>')
    # axes and gridlines
    steps = 5
    for k in range(steps + 1):
        y = ymin + (ymax - ymin) * k / steps
        out.append(f'<line x1="{L}" y1="{py(y):.1f}" x2="{W-R}" y2="{py(y):.1f}" stroke="#ddd"/>')
        out.append(f'<text x="{L-6}" y="{py(y)+4:.1f}" text-anchor="end">{fmt(y)}</text>')
    out.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H-B}" stroke="#333"/>')
    out.append(f'<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{H-B}" stroke="#333"/>')
    for i, x in enumerate(xs):
        out.append(f'<text x="{px(i):.1f}" y="{H-B+16}" text-anchor="middle">{html.escape(x)}</text>')
    out.append(f'<text x="{W/2}" y="{H-4}" text-anchor="middle">average chunk size</text>')
    out.append(f'<text transform="translate(12,{(T+H-B)/2}) rotate(-90)" text-anchor="middle">{html.escape(ylabel)}</text>')
    for label, colour, pts in series:
        path = ' '.join(f'{"M" if i == 0 else "L"}{px(i):.1f},{py(y):.1f}' for i, (_, y) in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2"/>')
        for i, (_, y) in enumerate(pts):
            out.append(f'<circle cx="{px(i):.1f}" cy="{py(y):.1f}" r="3.5" fill="{colour}"/>')
            out.append(f'<text x="{px(i):.1f}" y="{py(y)-8:.1f}" text-anchor="middle" fill="{colour}">{fmt(y)}</text>')
    # legend
    lx = L + 8
    for j, (label, colour, _) in enumerate(series):
        y = T + 10 + 16 * j
        out.append(f'<line x1="{lx}" y1="{y}" x2="{lx+18}" y2="{y}" stroke="{colour}" stroke-width="2"/>')
        out.append(f'<text x="{lx+24}" y="{y+4}">{html.escape(label)}</text>')
    out.append('</svg>')
    return '\n'.join(out)


def main(results, out):
    recs = load(results)
    corpora = ['rebuild', 'releases']
    sizes = sorted({avg for _, avg in recs})
    size_label = lambda a: f'{a // KIB} KiB'

    parts = []
    parts.append('<!doctype html><html><head><meta charset="utf-8"><title>Content-Derived Chunk Slicing — results</title>'
                 '<style>body{font-family:sans-serif;max-width:900px;margin:2em auto;line-height:1.45;color:#222}'
                 'table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #ccc;padding:4px 10px;text-align:right}'
                 'th{background:#f3f3f3}td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}'
                 '.charts{display:flex;flex-wrap:wrap;gap:12px}b.win{color:#0a7}code{background:#f3f3f3;padding:1px 4px}</style></head><body>')
    parts.append('<h1>Content-Derived Chunk Slicing — measured results</h1>')
    parts.append('<p><b>Idea.</b> Keep classic content-defined chunking with BLAKE3 chunk ids in a content-addressed '
                 'store, and add one manifest entry type: a <b>slice</b> <code>(chunk hash, offset, len)</code> that '
                 'references a byte range of a stored chunk. When an incoming chunk misses the store, compare it against '
                 'the stored chunks that its neighbouring hits point at, emit slices for the equal runs, and store only '
                 'the differing bytes. Full explanation in <code>README.md</code>; design record in <code>DESIGN.md</code>.</p>')
    parts.append('<p><b>Compression ratio</b> = bytes the store holds ÷ bytes ingested, over the whole corpus (lower is better). '
                 '<b>Metadata</b> counts 36 B per stored chunk, 16 B per whole-chunk reference, 20 B per slice. '
                 '<b>Entries per 64 KiB</b> = manifest entries per 64 KiB of file, i.e. range reads per 64 KiB reconstructed.</p>')

    # table
    parts.append('<h2>Results</h2><table><tr><th>corpus</th><th>avg chunk</th><th>cdc</th><th>cdc + slices</th>'
                 '<th>gain</th><th>entries / 64 KiB</th><th>metadata</th><th>miss rate</th><th>run time</th></tr>')
    for c in corpora:
        for avg in sizes:
            r = recs.get((c, avg))
            if r is None:
                continue
            a, b = r['systems']['cdc'], r['systems']['cdc+slices']
            parts.append(f'<tr><td>{c}</td><td>{size_label(avg)}</td><td>{a["ratio"]:.3f}</td>'
                         f'<td><b class="win">{b["ratio"]:.3f}</b></td><td>−{100*(a["ratio"]-b["ratio"]):.1f} pts</td>'
                         f'<td>{65536/avg:.1f} → {b["entries_per_64k"]:.1f}</td>'
                         f'<td>{a["meta_pct"]:.2f}% → {b["meta_pct"]:.2f}%</td>'
                         f'<td>{100*b["miss_rate"]:.0f}%</td><td>{r["seconds"]:.0f} s</td></tr>')
    parts.append('</table>')
    for c in corpora:
        parts.append(f'<p><b>{c}</b>: {CORPUS_DESC[c]}</p>')

    # charts
    parts.append('<h2>Charts</h2><div class="charts">')
    for c in corpora:
        pts_a = [(size_label(avg), recs[(c, avg)]['systems']['cdc']['ratio']) for avg in sizes if (c, avg) in recs]
        pts_b = [(size_label(avg), recs[(c, avg)]['systems']['cdc+slices']['ratio']) for avg in sizes if (c, avg) in recs]
        parts.append(svg_chart(f'{c}: storage ratio', [('cdc', '#c33', pts_a), ('cdc + slices', '#0a7', pts_b)],
                               'stored / ingested', 0.3, 0.9, lambda y: f'{y:.2f}'))
    for c in corpora:
        pts_a = [(size_label(avg), 65536 / avg) for avg in sizes if (c, avg) in recs]
        pts_b = [(size_label(avg), recs[(c, avg)]['systems']['cdc+slices']['entries_per_64k']) for avg in sizes if (c, avg) in recs]
        parts.append(svg_chart(f'{c}: manifest entries per 64 KiB', [('cdc', '#c33', pts_a), ('cdc + slices', '#0a7', pts_b)],
                               'range reads / 64 KiB', 0, 10, lambda y: f'{y:.1f}'))
    parts.append('</div>')

    # slice-length histogram for the recommended size
    parts.append('<h2>Slice lengths at 64 KiB</h2><p>Share of slices and of sliced bytes by slice length. '
                 'Long slices carry the bytes; <code>minslice</code> = 256 B bounds the entry count.</p>')
    for c in corpora:
        r = recs.get((c, 64 * KIB))
        if r is None or 'hist' not in r['systems']['cdc+slices']:
            continue
        hist = r['systems']['cdc+slices']['hist']
        tot_n = sum(v[0] for v in hist.values()) or 1
        tot_b = sum(v[1] for v in hist.values()) or 1
        parts.append(f'<table><tr><th colspan="3">{c}</th></tr><tr><th>slice length</th><th>slices</th><th>bytes</th></tr>')
        for k in sorted(hist, key=int):
            n, b = hist[k]; lo = 1 << int(k)
            parts.append(f'<tr><td>{lo:,} – {2*lo-1:,}</td><td>{100*n/tot_n:.1f}%</td><td>{100*b/tot_b:.1f}%</td></tr>')
        parts.append('</table>')

    parts.append('<h2>Reproduce</h2><p><code>./reproduce.sh</code> fetches the corpus listed in <code>corpus.txt</code> '
                 '(each NAR verified by sha256 against its <code>.narinfo</code>), runs <code>bench.py</code> for every '
                 'cell above, and renders this page. The Python is the readable specification of the algorithm.</p>')
    parts.append('</body></html>')
    open(out, 'w').write('\n'.join(parts))
    print(f'wrote {out}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'results.jsonl', sys.argv[2] if len(sys.argv) > 2 else 'report.html')
