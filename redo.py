"""redo.py <manifest> <chunk> <outdir>: before resuming a chunk, drop records that are not complete (manifest.should_rescreen)
so they are screened again by screen.py v3, and rewrite the chunk's text archive keeping only intact gzip members and
dropping the text of re-screened plats. v3 records are only dropped again when a newly repaired URL exists for a failed
download, so every plat is re-screened at most once (no loops). redo.json (the 2026-11-05 one-time list) is still honoured
for unversioned records."""
import gzip, json, os, sys, zlib
from manifest import files_for, should_rescreen
manifest, chunk, out = sys.argv[1], sys.argv[2], sys.argv[3]
redo = set(json.load(open('redo.json'))['seqs']) if os.path.exists('redo.json') else set()
fix = json.load(open('url_fix.json')) if manifest == 'plats' and os.path.exists('url_fix.json') else {}
rp, tp = files_for(manifest, chunk, out)
dropped = set()
if os.path.exists(rp):
    keep, latest = [], {}
    for l in open(rp):
        if not l.strip(): continue
        try: r = json.loads(l)
        except Exception: continue
        latest[r['seq']] = r          # last record per seq wins (same rule as merge.py)
    for s, r in latest.items():
        if should_rescreen(r, fix) or (s in redo and not r.get('v')): dropped.add(s); continue
        keep.append(json.dumps(r) + '\n')
    open(rp, 'w').writelines(keep)
damaged = False
if os.path.exists(tp):
    data = open(tp, 'rb').read(); off = 0; parts = []
    while off < len(data):
        d = zlib.decompressobj(31)
        try: parts.append(d.decompress(data[off:]))
        except zlib.error: damaged = True; break
        if not d.unused_data: break
        off = len(data) - len(d.unused_data)
    lines = {}
    for l in b''.join(parts).decode('utf-8', 'replace').splitlines():
        try: t = json.loads(l)
        except Exception: damaged = damaged or bool(l.strip()); continue
        if t.get('seq') not in dropped: lines[t['seq']] = json.dumps(t) + '\n'
    if dropped or damaged:
        with gzip.open(tp, 'wt') as f: f.writelines(lines.values())
print(f'redo {manifest} chunk {chunk}: dropped {len(dropped)} records for re-screening' + ('; repaired damaged text archive' if damaged else ''), flush=True)
