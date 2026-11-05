"""Screen Benton County plats for easements.

usage: python screen.py <chunk> <n_chunks> <outdir>

Takes rows of plats.csv where seq % n_chunks == chunk, downloads each PDF from the county (politely),
OCRs every page at screening resolution and writes:
  <outdir>/results_<chunk>.jsonl   one record per plat: status, pages, easement flags, callouts, key phrases
  <outdir>/text_<chunk>.jsonl.gz   raw OCR text per page (for later targeted reading)
"""
import csv, gzip, json, multiprocessing, os, re, subprocess, sys, tempfile, time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
import pymupdf
from callouts import callouts, ue_widths

chunk, n, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
os.makedirs(out, exist_ok=True)
allrows = list(csv.DictReader(open('plats.csv')))
# contiguous blocks so chunk order follows plats.csv priority order (Bentonville, Cave Springs, Centerton, rural, other)
rows = [r for r in allrows if int(r['seq']) * n // len(allrows) == chunk]
LIMIT = float(os.environ.get('TIME_LIMIT_MIN', '330')) * 60
T0 = time.time()
PLAT_WORKERS = 4  # plats in flight at once (runner has 4 vCPUs; also overlaps downloads)
PAGE_WORKERS = 2  # pages OCR'd at once within a plat
# Ubuntu's tesseract is built with OpenMP; several instances at once with default threading thrash the CPU
# and hit the 300 s timeout (multi-page plats were losing most pages). One thread per tesseract process.
os.environ['OMP_THREAD_LIMIT'] = '1'

KEY = {
    'easement': r'EASEMENT|ESM[\'’]?T|\bU\.?\s?/?\s?E\b|\bD\.?\s?E\b|\bA\.?\s?E\b',
    'utility_easement': r'UTILITY\s+(?:EASEMENT|ESM)|\bU\.\s?E\.|\bU/E\b|\bU\.E\b|GENERAL\s+UTILITY',
    'general_utility': r'GENERAL\s+UTILITY|ALL\s+UTILITIES|PUBLIC\s+UTILIT|FRANCHISE',
    'restricted': r'RESERVED\s+FOR\s+(?:THE\s+)?CITY|CITY\s+UTILITY\s+EASEMENT|FOR\s+THE\s+EXCLUSIVE|EXCLUSIVE\s+USE',
    'telecom_named': r'TELEPHONE|TELECOMMUNICATION|COMMUNICATION|CABLE\s+TV|CABLE\s+TELEVISION|FIBER|AT&T|COX\b|OZARKSGO|OZARKS\s+GO',
    'electric_named': r'CARROLL\s+ELECTRIC|OZARKS\s+ELECTRIC|SWEPCO|SOUTHWESTERN\s+ELECTRIC|ENTERGY',
    'private_street': r'PRIVATE\s+(?:STREET|DRIVE|ROAD|LANE)|STREETS?\s+(?:ARE|SHALL\s+BE)\s+PRIVATE',
    'drainage_only': r'DRAINAGE\s+EASEMENT',
    'release_vacate': r'RELEASE\s+OF\s+EASEMENT|EASEMENT\s+RELEASE|VACAT',
    'separate_instrument': r'SEPARATE\s+INSTRUMENT|DOC(?:UMENT)?\.?\s*(?:NO\.?|#)\s*[L\d]|BOOK\s+\d+\s*,?\s*PAGE|BK\.?\s*\d+\s*PG',
    'dedication': r'HEREBY\s+DEDICATE|DEDICATED\s+(?:TO|BY)\s+THIS',
    'row': r'R\s?/\s?W|R\.O\.W|RIGHT[\s-]*OF[\s-]*WAY',
}
PHRASE = re.compile(r'[^.\n]{0,120}(?:EASEMENT|U\.\s?E\.|U/E|RESERVED FOR|FRANCHISE|PRIVATE STREET)[^.\n]{0,160}', re.I)

def fetch(url, path):
    for u in (url, url[:-4] + '.PDF', url[:-4] + '.pdf'):
        for attempt in range(3):
            r = subprocess.run(['curl', '-s', '-L', '-m', '300', '-A', 'benton-plat-screening (public-records research)',
                                '-o', path, u.replace(' ', '%20')])
            if os.path.exists(path) and os.path.getsize(path) > 1000 and open(path, 'rb').read(5) == b'%PDF-':
                return True
            time.sleep(3 + 5 * attempt)
    return False

def ocr_page(args):
    path, i = args
    doc = pymupdf.open(path); pg = doc[i]
    longest = max(pg.rect.width, pg.rect.height) / 72.0  # inches
    for dpi in (200, 140, 100):
        dpi_eff = min(dpi, int(9000 / max(longest, 1)))
        if dpi_eff < 60: dpi_eff = 60
        pix = pg.get_pixmap(dpi=dpi_eff, colorspace=pymupdf.csGRAY)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f: png = f.name
        pix.save(png)
        try:
            r = subprocess.run(['tesseract', png, '-', '--psm', '11'], capture_output=True, timeout=300)
            return r.stdout.decode('utf8', 'ignore'), dpi_eff
        except subprocess.TimeoutExpired:
            continue
        finally:
            os.remove(png)
    return '', 0

prev = set()
if os.path.exists(f'{out}/results_{chunk}.jsonl'):
    for l in open(f'{out}/results_{chunk}.jsonl'):
        try: prev.add(json.loads(l)['seq'])
        except Exception: pass
rows = [r for r in rows if int(r['seq']) not in prev]
print('resuming chunk', chunk, 'skipping', len(prev), 'done; remaining', len(rows), flush=True)
def screen_plat(row):
    """Download and OCR one plat. Returns (results record, text record or None)."""
    rec = dict(seq=int(row['seq']), area=row['area'], oid=int(row['oid']), image=row['image'], url=row['url'], type=row['type'],
               subdivision=row['subdivision'], folder=row['folder'], date=row['date'], v=2)
    with tempfile.TemporaryDirectory() as td:
        pdf = f'{td}/p.pdf'
        if not fetch(row['url'], pdf):
            rec.update(status='DOWNLOAD_FAILED'); return rec, None
        try:
            np = len(pymupdf.open(pdf))
        except Exception:
            rec.update(status='PDF_UNREADABLE'); return rec, None
        with ThreadPoolExecutor(PAGE_WORKERS) as ex:
            pages = list(ex.map(ocr_page, [(pdf, i) for i in range(min(np, 20))]))
    text = '\n'.join(t for t, _ in pages)
    up = text.upper()
    flags = {k: len(re.findall(p, up)) for k, p in KEY.items()}
    co = callouts(text)
    ue = ue_widths(co)
    rec.update(status='OK' if text.strip() else 'NO_TEXT', pages=np, chars=len(text), dpi=[d for _, d in pages],
               flags=flags, ue_widths={str(k): v for k, v in ue.most_common(8)},
               callouts=[f'{w:g} {k} x{c}' for (k, w), c in co.most_common(20)],
               phrases=[m.group(0).strip()[:280] for m in PHRASE.finditer(text)][:25],
               shows_easements=bool(flags['easement'] or flags['utility_easement']),
               shows_utility_easement=bool(flags['utility_easement']))
    time.sleep(1.0)  # be polite to the county server
    return rec, dict(seq=rec['seq'], image=row['image'], pages=[t for t, _ in pages])

res = open(f'{out}/results_{chunk}.jsonl', 'a')
txt = gzip.open(f'{out}/text_{chunk}.jsonl.gz', 'at')

def write(fut):
    rec, t = fut.result()
    res.write(json.dumps(rec) + '\n'); res.flush()
    if t is not None: txt.write(json.dumps(t) + '\n')

# Up to PLAT_WORKERS plats in flight; the time limit is checked before each plat is started, as before,
# and plats already started are finished and written. Records are written as they complete (merge sorts by seq).
with ProcessPoolExecutor(PLAT_WORKERS, mp_context=multiprocessing.get_context('fork')) as pool:
    pending = set()
    for row in rows:
        if time.time() - T0 > LIMIT:
            print('time limit reached', flush=True); break
        if len(pending) >= PLAT_WORKERS:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in finished: write(f)
            if time.time() - T0 > LIMIT:
                print('time limit reached', flush=True); break
        pending.add(pool.submit(screen_plat, row))
    for f in as_completed(pending): write(f)
res.close(); txt.close()
print('chunk', chunk, 'processed', sum(1 for _ in open(f'{out}/results_{chunk}.jsonl')), 'of', len(rows), round((time.time() - T0) / 60), 'min')
