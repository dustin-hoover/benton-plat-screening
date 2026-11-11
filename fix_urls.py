"""fix_urls.py [server_index.json.gz]: repair manifest URLs that 404 on the county server.

For every plats.csv row whose record is DOWNLOAD_FAILED (in results.jsonl), look for the file on the server
(server_index.json.gz from crawl.py) and write url_fix.json: {seq: {"url": ..., "basis": ...}}.
screen.py downloads from the fixed URL. A repair is accepted ONLY when it is unambiguous:
  EXACT     same file name (case-insensitive, extension ignored, doubled extensions removed)
  PUNCT     names identical once punctuation/whitespace is removed (stray '\\n', '.pd', '-bad', '2003-10-14' vs '2003-1014')
  INSTR     same recording-instrument number (last '_' token, >=4 chars) AND name similarity >= 0.85 AND only one such file
When several files qualify, prefer the record's own folder, then PDF over TIF/JPG, then non-backup paths, then the largest.
Anything weaker (e.g. a neighbouring instrument number) is NOT linked; it is listed in url_unresolved.json for manual
follow-up. Those server files are still screened through the extra manifest (build_extra.py), so nothing is skipped."""
import collections, difflib, gzip, json, os, re, sys
from urllib.parse import quote

BASE = 'https://gis.bentoncountyar.gov'
IMG = re.compile(r'\.(pdf|tif|tiff|jpg|jpeg|png)$', re.I)

def key(name):
    n = os.path.basename(name.strip()).lower()
    while IMG.search(n): n = IMG.sub('', n)
    return n.strip()

def bare(k): return re.sub(r'[^a-z0-9]', '', k)

def rank(f, folder):
    p = f['path'].lower(); ext = os.path.splitext(p)[1]
    return (p.split('/')[2] == folder.lower(), ext == '.pdf', 'backup' not in p, f['size'])

def load_index(path):
    return [f for f in json.load(gzip.open(path))['files'] if IMG.search(f['path'])]

def url_of(path): return BASE + quote(path)

if __name__ == '__main__':
    files = load_index(sys.argv[1] if len(sys.argv) > 1 else 'server_index.json.gz')
    by_key, by_bare, by_suf = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    for f in files:
        k = key(f['path']); by_key[k].append(f); by_bare[bare(k)].append(f); by_suf[k.split('_')[-1]].append(f)
    failed = [r for r in map(json.loads, open('results.jsonl')) if r['status'] == 'DOWNLOAD_FAILED']
    fix, unresolved = {}, []
    for r in failed:
        k = key(r['image'])
        c, basis = by_key.get(k) or by_key.get(key(r['url'])), 'EXACT'
        if not c: c, basis = by_bare.get(bare(k)), 'PUNCT'
        if not c:
            suf = k.split('_')[-1]
            if len(suf) >= 4:
                c = [f for f in by_suf.get(suf, []) if difflib.SequenceMatcher(None, k, key(f['path'])).ratio() >= 0.85]
                c = c if len({key(f['path']) for f in c}) == 1 else None
                basis = 'INSTR'
        if not c:
            close = difflib.get_close_matches(k, list(by_key), 3, 0.8)
            unresolved.append(dict(seq=r['seq'], image=r['image'], folder=r['folder'], type=r['type'], url=r['url'],
                                   nearest=[by_key[x][0]['path'] for x in close]))
            continue
        best = max(c, key=lambda f: rank(f, r['folder']))
        fix[str(r['seq'])] = dict(url=url_of(best['path']), basis=basis, was=r['url'])
    json.dump(fix, open('url_fix.json', 'w'), indent=0)
    json.dump(unresolved, open('url_unresolved.json', 'w'), indent=1)
    print(f'{len(failed)} failed downloads: {len(fix)} repaired', dict(collections.Counter(v["basis"] for v in fix.values())),
          f'; {len(unresolved)} unresolved (url_unresolved.json)')
