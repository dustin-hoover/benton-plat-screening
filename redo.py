"""redo.py <chunk> <outdir>: before resuming a chunk, drop old (unstamped) records for plats listed in redo.json so they are
re-screened, and rewrite the chunk's text archive keeping only intact gzip members. Records written by the current screen.py
carry v=2 and are never dropped, so each listed plat is re-screened once."""
import gzip, json, os, sys, zlib
chunk, out = sys.argv[1], sys.argv[2]
redo = set(json.load(open('redo.json'))['seqs']) if os.path.exists('redo.json') else set()
rp, tp = f'{out}/results_{chunk}.jsonl', f'{out}/text_{chunk}.jsonl.gz'
dropped = set()
if os.path.exists(rp):
    keep = []
    for l in open(rp):
        if not l.strip(): continue
        try: r = json.loads(l)
        except Exception: continue
        if r.get('seq') in redo and not r.get('v'): dropped.add(r['seq']); continue
        keep.append(l if l.endswith('\n') else l + '\n')
    open(rp, 'w').writelines(keep)
if os.path.exists(tp):
    data = open(tp, 'rb').read(); off = 0; parts = []; damaged = False
    while off < len(data):
        d = zlib.decompressobj(31)
        try: parts.append(d.decompress(data[off:]))
        except zlib.error: damaged = True; break
        if not d.unused_data: break
        off = len(data) - len(d.unused_data)
    lines = []
    for l in b''.join(parts).decode('utf-8', 'replace').splitlines():
        try: t = json.loads(l)
        except Exception: damaged = damaged or bool(l.strip()); continue
        if t.get('seq') not in dropped: lines.append(json.dumps(t) + '\n')
    if dropped or damaged:
        with gzip.open(tp, 'wt') as f: f.writelines(lines)
print(f'redo chunk {chunk}: dropped {len(dropped)} records for re-screening' + ('; repaired damaged text archive' if os.path.exists(tp) and damaged else ''), flush=True)
