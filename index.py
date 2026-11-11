"""index.py [out.json.gz]: pull the county Plat Scans index (Cadastral MapServer layer 11, attributes only) and write
plat_index.json.gz: {server path (lower case): {oid, type, subdivision, date, folder}}. build_extra.py uses it to give
files that are in the live index but not in plats.csv (newly recorded plats) their county type, subdivision and date."""
import gzip, json, subprocess, sys, time, urllib.parse
from urllib.parse import unquote, urlparse
URL = 'https://gis.bentoncountyar.gov/arcgis/rest/services/Basemaps/Cadastral/MapServer/11/query'

def post(params):
    data = urllib.parse.urlencode(dict(params, f='json'))
    for i in range(8):
        r = subprocess.run(['curl', '-s', '-m', '240', '-X', 'POST', URL, '--data-binary', data,
                            '-H', 'Content-Type: application/x-www-form-urlencoded'], capture_output=True)
        try:
            d = json.loads(r.stdout)
            if 'error' not in d: return d
        except Exception: pass
        time.sleep(2 + 3 * i)
    raise RuntimeError('county index query failed')

ids = sorted(post(dict(where='1=1', returnIdsOnly='true'))['objectIds'])
out, todo, chunk = {}, ids, 500
while todo:
    nxt = []
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        fs = post(dict(objectIds=','.join(map(str, part)), returnGeometry='false',
                       outFields='OBJECTID,SUBDIVISION,COMMENT,DATE_OF_PLAT,FOLDER,HYPEXT'))['features']
        got = set()
        for f in fs:
            a = f['attributes']; got.add(a['OBJECTID'])
            if not a.get('HYPEXT'): continue
            d = a.get('DATE_OF_PLAT')
            out[unquote(urlparse(a['HYPEXT'].strip()).path).lower()] = dict(
                oid=a['OBJECTID'], type=(a.get('COMMENT') or '').strip(), subdivision=(a.get('SUBDIVISION') or '').strip(),
                folder=(a.get('FOLDER') or '').strip(), date=time.strftime('%Y-%m-%d', time.gmtime(d / 1000)) if d else '')
        nxt += [o for o in part if o not in got]
    if not nxt or chunk == 1: break
    todo, chunk = nxt, max(1, chunk // 4)
json.dump(out, gzip.open(sys.argv[1] if len(sys.argv) > 1 else 'plat_index.json.gz', 'wt'))
print(len(ids), 'index records;', len(out), 'with a file path')
