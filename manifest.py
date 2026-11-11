"""Shared manifest helpers.

Two manifests are screened:
  plats   plats.csv  county Plat Scans index. Chunk = seq*n//len (contiguous, priority order). Files results_<c>.jsonl, text_<c>.jsonl.gz
  extra   extra.csv  files on the county server that the index does not reference (build_extra.py). Each row carries a fixed
                     'chunk' column so appending new files never reshuffles finished chunks. Files extra_results_<c>.jsonl, extra_text_<c>.jsonl.gz
"""
import csv

MANIFESTS = {'plats': ('plats.csv', ''), 'extra': ('extra.csv', 'extra_')}
TARGET_DPI = 200
MAX_PX = 12000      # longest side in pixels; a 60-inch sheet still gets the full 200 dpi
LOW_TEXT = 200      # a plat with fewer OCR characters than this gets a 300-dpi second pass


def rows_for(manifest, chunk, n):
    path, _ = MANIFESTS[manifest]
    rows = list(csv.DictReader(open(path)))
    if manifest == 'plats':
        return [r for r in rows if int(r['seq']) * n // len(rows) == chunk]
    return [r for r in rows if int(r['chunk']) == chunk]


def files_for(manifest, chunk, out='out'):
    pre = MANIFESTS[manifest][1]
    return f'{out}/{pre}results_{chunk}.jsonl', f'{out}/{pre}text_{chunk}.jsonl.gz'


def expected_dpi(page_inches):
    return max(60, min(TARGET_DPI, int(MAX_PX / max(page_inches, 1))))


def incomplete_reason(r, fix=None):
    """Why a screening record is not complete (None when complete).

    Complete means: the file was downloaded and opened; every page was OCR'd at the full target resolution
    (200 dpi, or the pixel cap on very large sheets) without a timeout or error; no page cap; and a plat with
    little text also got the 300-dpi second pass. Records from screen.py v3 carry their own page_status and
    'complete' flag. Older records (v2/unversioned) are judged from what they recorded and re-screened if in doubt.
    """
    st = r.get('status')
    if st == 'DOWNLOAD_FAILED':
        if fix and str(r['seq']) in fix and fix[str(r['seq'])]['url'] != r.get('url'):
            return 'download_failed_repairable'
        return 'download_failed'
    if st == 'PDF_UNREADABLE':
        return 'unreadable'
    if (r.get('v') or 0) >= 3:
        if r.get('complete'):
            return None
        bad = [s for s in r.get('page_status', []) if s != 'ok']
        return f'pages_degraded_after_retry:{len(bad)}' if bad else 'incomplete_v3'
    # legacy records
    dpi = r.get('dpi') or []
    if st == 'NO_TEXT':
        return 'legacy_no_text'
    if (r.get('pages') or 0) > 20:
        return 'legacy_page_cap'
    if any(d == 0 for d in dpi):
        return 'legacy_blank_page'
    if any(d < TARGET_DPI for d in dpi):
        return 'legacy_low_dpi'
    if (r.get('chars') or 0) < LOW_TEXT:
        return 'legacy_low_text'
    return None


def should_rescreen(r, fix=None):
    """True when a record should be dropped and screened again (each plat at most once more under v3, except when a
    newly repaired URL appears for a failed download)."""
    why = incomplete_reason(r, fix)
    if why is None:
        return False
    if why.startswith('legacy_') or why == 'download_failed_repairable':
        return True
    return why in ('download_failed', 'unreadable') and (r.get('v') or 0) < 3   # retry once, in case it was temporary
