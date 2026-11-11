"""build_extra.py [server_index.json.gz]: list every plat-like file on the county server (PDF/TIF/JPG/PNG) that the county
Plat Scans index (plats.csv, plus repairs in url_fix.json) does not reference, and write extra.csv for screening.

- Byte-identical duplicates (same file name and same size as a referenced file) are skipped; the duplicate list is kept
  in extra_duplicates.json.gz so the decision is auditable.
- Same name but different size is kept (it may be a different scan or revision).
- Stable ids: existing extra.csv rows keep their seq/chunk; new files are appended with the next seq, chunk = seq % N_EXTRA.
  So re-running after a new crawl never reshuffles finished chunks.
- Files that ARE in the live county index (plat_index.json.gz from index.py) but not in plats.csv, i.e. plats recorded
  after plats.csv was built, carry the index's OBJECTID, type, subdivision and date.
- Rows are written in priority order (priority cities, other cities, rural, state/highway/other records, Corner Scans last);
  each chunk screens its own rows in that order.
"""
import csv, gzip, json, os, re, sys
from urllib.parse import unquote, urlparse
from fix_urls import key, load_index, url_of

N_EXTRA = 50
SEQ0 = 1_000_000
CITY = {'BENTONVILLE': 'Bentonville', 'CAVE_SPRINGS': 'Cave Springs', 'CENTERTON': 'Centerton'}
TAIL = ['RURAL', 'RURBAN', 'State Filed Plats', 'All_New_Plats', 'CORPS', 'ECMP', 'AHTD', 'Not_Found_Not_Indexable',
        'OUTSIDE_BENTON_CO', 'Corner Scans']
COLS = ['seq', 'chunk', 'area', 'priority', 'oid', 'image', 'url', 'type', 'subdivision', 'folder', 'date', 'size', 'path']


def server_path(url):
    p = unquote(urlparse(url.strip()).path)
    return p.lower()


def folder_rank(top):
    if top in CITY: return (0, list(CITY).index(top))
    if top in TAIL: return (2, TAIL.index(top))
    return (1, 0)


def guess_type(path):
    p = path.upper()
    if '/CORNER SCANS/' in p: return 'CORNER RECORD'
    if '/AHTD/' in p: return 'HIGHWAY ROW PLAN'
    if '/STATE FILED PLATS/' in p: return 'STATE FILED PLAT'
    if 'PRELIM' in p: return 'PRELIMINARY'
    return 'UNINDEXED'


def iso(d):
    m, dd, y = d.split('/'); return f'{y}-{int(m):02d}-{int(dd):02d}'


if __name__ == '__main__':
    files = load_index(sys.argv[1] if len(sys.argv) > 1 else 'server_index.json.gz')
    fix = json.load(open('url_fix.json')) if os.path.exists('url_fix.json') else {}
    ref_paths = {server_path(r['url']) for r in csv.DictReader(open('plats.csv'))} | {server_path(v['url']) for v in fix.values()}
    ref_files = [f for f in files if f['path'].lower() in ref_paths]
    ref_keysize = {(key(f['path']), f['size']) for f in ref_files}
    old = {r['path']: r for r in csv.DictReader(open('extra.csv'))} if os.path.exists('extra.csv') else {}
    nxt = max([int(r['seq']) for r in old.values()] + [SEQ0 - 1]) + 1
    rows, dups = [], []
    for f in files:
        if f['path'].lower() in ref_paths: continue
        if (key(f['path']), f['size']) in ref_keysize: dups.append(f['path']); continue
        top = f['path'].split('/')[2] if f['path'].count('/') > 2 else ''
        if f['path'] in old:
            rows.append(old[f['path']]); continue
        rows.append(dict(seq=nxt, chunk=nxt % N_EXTRA, area=CITY.get(top, 'Rural' if top in ('RURAL', 'RURBAN') else 'Other'),
                         priority=str(folder_rank(top)[0]), oid='-1', image=os.path.splitext(os.path.basename(f['path']))[0],
                         url=url_of(f['path']), type=guess_type(f['path']), subdivision='', folder=top or '(root)',
                         date=iso(f['date']), size=f['size'], path=f['path']))
        nxt += 1
    # newest first within a folder
    idx = json.load(gzip.open('plat_index.json.gz')) if os.path.exists('plat_index.json.gz') else {}
    n_idx = 0
    for r in rows:   # files in the live county index but not in plats.csv (recorded after it was built): use the index's metadata
        a = idx.get(r['path'].lower())
        if a:
            n_idx += 1
            r.update(oid=str(a['oid']), type=a['type'] or r['type'], subdivision=a['subdivision'], date=a['date'] or r['date'])
    rows.sort(key=lambda r: r['date'], reverse=True)
    rows.sort(key=lambda r: folder_rank(r['folder']))
    with open('extra.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader(); w.writerows(rows)
    json.dump(sorted(dups), gzip.open('extra_duplicates.json.gz', 'wt'))
    from collections import Counter
    print(f'{n_idx} of them are in the live county index (recorded after plats.csv was built)')
    print(f'{len(files)} plat-like server files; {len(ref_files)} referenced by the index; {len(dups)} byte-identical duplicates skipped;'
          f' {len(rows)} unindexed files in extra.csv ({len(rows) - len(old)} new)')
    print(Counter(r['folder'] for r in rows).most_common(25))
    print(Counter(os.path.splitext(r['path'])[1].lower() for r in rows))
