"""crawl.py [out.json.gz]: crawl the county plat server's IIS directory listings (gis.bentoncountyar.gov/plats/)
and write every file found as {path, size, date} to server_index.json.gz. Paced and single-threaded-ish
(4 requests in flight) so the county server isn't hammered. Used by fix_urls.py and build_extra.py."""
import gzip, html, json, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

BASE = 'https://gis.bentoncountyar.gov'
OUT = sys.argv[1] if len(sys.argv) > 1 else 'server_index.json.gz'
ROW = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+(&lt;dir&gt;|\d+)\s+<A HREF="([^"]+)">', re.I)

def get(path):
    for attempt in range(4):
        r = subprocess.run(['curl', '-s', '-m', '120', '-A', 'benton-plat-screening (public-records research)',
                            BASE + path.replace(' ', '%20')], capture_output=True)
        if r.returncode == 0 and b'<pre>' in r.stdout:
            return r.stdout.decode('utf8', 'replace')
        time.sleep(2 + 4 * attempt)
    return None

files, failed, todo, seen = [], [], ['/plats/'], set()
with ThreadPoolExecutor(4) as ex:
    while todo:
        batch, todo = todo, []
        for path, page in zip(batch, ex.map(get, batch)):
            if page is None: failed.append(path); continue
            for date, tm, size, href in ROW.findall(page):
                href = html.unescape(href)
                if not href.lower().startswith('/plats/'): continue
                if size.startswith('&lt;'):
                    if href not in seen: seen.add(href); todo.append(href)
                else:
                    files.append(dict(path=unquote(href), size=int(size), date=date))
        print(f'dirs {len(seen)+1} files {len(files)} pending {len(todo)} failed {len(failed)}', flush=True)
json.dump(dict(crawled=time.strftime('%Y-%m-%d'), dirs=len(seen) + 1, failed_dirs=failed, files=files),
          gzip.open(OUT, 'wt'))
print('wrote', OUT, len(files), 'files;', len(failed), 'dirs failed')
if failed: sys.exit(1)
