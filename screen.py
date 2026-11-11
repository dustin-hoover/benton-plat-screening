"""Screen Benton County plats for easements (v3).

usage: python screen.py <chunk> <n_chunks> <outdir>        env MANIFEST=plats|extra (default plats)

Takes the chunk's rows of the manifest (see manifest.py), downloads each file from the county (politely; PDF, TIF, JPG
or PNG), OCRs every page and writes:
  <outdir>/[extra_]results_<chunk>.jsonl   one record per file: status, pages, easement flags, callouts, key phrases
  <outdir>/[extra_]text_<chunk>.jsonl.gz   raw OCR text per page (for later targeted reading)

v3 (2026-11-10), so that a record is either complete or says exactly what is missing:
  - every page, no page cap;
  - each page OCR'd at 200 dpi; pages too big for one image (> MAX_PX) are OCR'd in overlapping tiles instead of being shrunk;
  - a timed-out page/tile is retried alone with a long timeout before any lower-resolution fallback,
    and the fallback is recorded as 'degraded', never silently;
  - plats with little text get a second pass at 300 dpi and keep whichever reading has more text;
  - repaired URLs from url_fix.json; per-page page_status / dpi / dpi_target; a 'complete' flag.
"""
import csv, gzip, json, multiprocessing, os, re, subprocess, sys, tempfile, time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
import pymupdf
from callouts import callouts, ue_widths
from manifest import LOW_TEXT, MAX_PX, TARGET_DPI, files_for, rows_for

chunk, n, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
MANIFEST = os.environ.get('MANIFEST', 'plats')
os.makedirs(out, exist_ok=True)
rows = rows_for(MANIFEST, chunk, n)
FIX = json.load(open('url_fix.json')) if MANIFEST == 'plats' and os.path.exists('url_fix.json') else {}
LIMIT = float(os.environ.get('TIME_LIMIT_MIN', '330')) * 60
T0 = time.time()
PLAT_WORKERS = 4  # plats in flight at once (runner has 4 vCPUs; also overlaps downloads)
PAGE_WORKERS = 2  # pages OCR'd at once within a plat
TIMEOUT, RETRY_TIMEOUT = 300, 1500
# Ubuntu's tesseract is built with OpenMP; several instances at once with default threading thrash the CPU.
os.environ['OMP_THREAD_LIMIT'] = '1'
pymupdf.TOOLS.mupdf_display_errors(False)

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
MAGIC = {b'%PDF-': '.pdf', b'II*\x00': '.tif', b'MM\x00*': '.tif', b'\xff\xd8\xff': '.jpg', b'\x89PNG': '.png'}


def kind(path):
    head = open(path, 'rb').read(8)
    return next((ext for m, ext in MAGIC.items() if head.startswith(m)), None)


def fetch(url, td):
    """Download to td; returns the local path with the right extension, or None."""
    tmp = f'{td}/dl'
    stem, ext = os.path.splitext(url)
    alts = [url] + ([stem + '.PDF', stem + '.pdf'] if ext.lower() == '.pdf' else [])
    for u in dict.fromkeys(alts):
        for attempt in range(3):
            r = subprocess.run(['curl', '-s', '-L', '-m', '600', '-A', 'benton-plat-screening (public-records research)',
                                '-o', tmp, u.replace(' ', '%20')])
            if os.path.exists(tmp) and os.path.getsize(tmp) > 1000 and (k := kind(tmp)):
                os.replace(tmp, f'{td}/p{k}'); return f'{td}/p{k}'
            time.sleep(3 + 5 * attempt)
    return None


def tesseract(png, timeout):
    r = subprocess.run(['tesseract', png, '-', '--psm', '11'], capture_output=True, timeout=timeout)
    return r.stdout.decode('utf8', 'ignore')


def ocr_at(pg, dpi, timeout):
    """OCR a page at dpi, in overlapping tiles when it would exceed MAX_PX. Raises TimeoutExpired."""
    w, h = pg.rect.width, pg.rect.height
    nx = max(1, -(-int(w / 72 * dpi) // MAX_PX)); ny = max(1, -(-int(h / 72 * dpi) // MAX_PX))
    ov = 0.03
    texts = []
    for iy in range(ny):
        for ix in range(nx):
            clip = pymupdf.Rect(pg.rect.x0 + w * max(0, ix / nx - ov), pg.rect.y0 + h * max(0, iy / ny - ov),
                                pg.rect.x0 + w * min(1, (ix + 1) / nx + ov), pg.rect.y0 + h * min(1, (iy + 1) / ny + ov))
            pix = pg.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY)
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f: png = f.name
            try:
                pix.save(png); del pix
                texts.append(tesseract(png, timeout))
            finally:
                os.remove(png)
    return '\n'.join(texts), nx * ny


def ocr_page(args):
    """Returns dict(text, dpi, dpi_target, status, tiles). status: ok | retried | degraded | failed."""
    path, i, dpi_force = args
    doc = pymupdf.open(path); pg = doc[i]
    # Always 200 dpi (300 on the low-text pass). Tested 2026-11-10: upscaling scans whose embedded image claims a low
    # resolution still reads more words than rendering at that resolution, so the scan's own dpi is not used as a ceiling.
    target = dpi_force or TARGET_DPI
    for timeout, status in ((TIMEOUT, 'ok'), (RETRY_TIMEOUT, 'retried')):
        if status == 'retried' and time.time() - T0 > LIMIT + 15 * 60:
            break  # too close to the job timeout for a long retry; fall back (recorded as degraded)
        try:
            t, tiles = ocr_at(pg, target, timeout)
            return dict(text=t, dpi=target, dpi_target=target, status=status, tiles=tiles)
        except subprocess.TimeoutExpired:
            continue
        except Exception:  # render/tesseract error: fall through to the fallback
            break
    for dpi in (140, 100):  # last resort, recorded as degraded
        if dpi >= target: continue
        try:
            t, tiles = ocr_at(pg, dpi, RETRY_TIMEOUT)
            return dict(text=t, dpi=dpi, dpi_target=target, status='degraded', tiles=tiles)
        except Exception:
            continue
    return dict(text='', dpi=0, dpi_target=target, status='failed', tiles=0)


prev = set()
RES, TXT = files_for(MANIFEST, chunk, out)
if os.path.exists(RES):
    for l in open(RES):
        try: prev.add(json.loads(l)['seq'])
        except Exception: pass
rows = [r for r in rows if int(r['seq']) not in prev]
print('resuming', MANIFEST, 'chunk', chunk, 'skipping', len(prev), 'done; remaining', len(rows), flush=True)


def ocr_doc(path, npages, dpi_force=None):
    with ThreadPoolExecutor(PAGE_WORKERS) as ex:
        return list(ex.map(ocr_page, [(path, i, dpi_force) for i in range(npages)]))


def screen_plat(row):
    """Download and OCR one file. Returns (results record, text record or None)."""
    fix = FIX.get(row['seq'])
    url = fix['url'] if fix else row['url']
    rec = dict(seq=int(row['seq']), area=row['area'], oid=int(row['oid']), image=row['image'], url=url, type=row['type'],
               subdivision=row['subdivision'], folder=row['folder'], date=row['date'], v=3)
    if fix: rec.update(url_fixed_from=row['url'], url_fix_basis=fix['basis'])
    with tempfile.TemporaryDirectory() as td:
        path = fetch(url, td)
        if not path:
            rec.update(status='DOWNLOAD_FAILED', complete=False); return rec, None
        rec['file_type'] = os.path.splitext(path)[1][1:]
        try:
            np = len(pymupdf.open(path))
        except Exception:
            rec.update(status='PDF_UNREADABLE', complete=False); return rec, None
        pages = ocr_doc(path, np)
        if sum(len(p['text']) for p in pages) < LOW_TEXT:
            hi = ocr_doc(path, np, 300)
            better = sum(len(p['text']) for p in hi) > sum(len(p['text']) for p in pages)
            rec['low_text_pass'] = '300dpi_kept' if better else '300dpi_no_gain'
            if better: pages = hi
    text = '\n'.join(p['text'] for p in pages)
    up = text.upper()
    flags = {k: len(re.findall(p, up)) for k, p in KEY.items()}
    co = callouts(text)
    ue = ue_widths(co)
    pst = [p['status'] for p in pages]
    rec.update(status='OK' if text.strip() else 'NO_TEXT', pages=np, chars=len(text),
               dpi=[p['dpi'] for p in pages], dpi_target=[p['dpi_target'] for p in pages], page_status=pst,
               tiles=sum(p['tiles'] for p in pages), complete=all(s in ('ok', 'retried') for s in pst),
               flags=flags, ue_widths={str(k): v for k, v in ue.most_common(8)},
               callouts=[f'{w:g} {k} x{c}' for (k, w), c in co.most_common(20)],
               phrases=[m.group(0).strip()[:280] for m in PHRASE.finditer(text)][:25],
               shows_easements=bool(flags['easement'] or flags['utility_easement']),
               shows_utility_easement=bool(flags['utility_easement']))
    time.sleep(1.0)  # be polite to the county server
    return rec, dict(seq=rec['seq'], image=row['image'], pages=[p['text'] for p in pages])


res = open(RES, 'a')
txt = gzip.open(TXT, 'at')


def write(fut):
    rec, t = fut.result()
    res.write(json.dumps(rec) + '\n'); res.flush()
    if t is not None: txt.write(json.dumps(t) + '\n'); txt.flush()


# Up to PLAT_WORKERS files in flight; the time limit is checked before each file is started, and files already
# started are finished and written. Records are written as they complete (merge sorts by seq).
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
print(MANIFEST, 'chunk', chunk, 'records', sum(1 for _ in open(RES)), round((time.time() - T0) / 60), 'min')
