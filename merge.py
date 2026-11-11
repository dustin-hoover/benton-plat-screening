"""merge.py <dir>: merge per-chunk screening results into results.jsonl (county index manifest), extra_results.jsonl
(unindexed server files), summary.json, and completeness.json (the audit gate).

completeness.json says, for each manifest, how many records are complete and why the rest are not
(manifest.incomplete_reason), and lists what still needs a person: download failures with no repairable URL, pages
that stayed degraded after the long retry, and files with little or no OCR text even after the 300-dpi pass
(candidates for visual reading: hand-drafted plats, faint scans)."""
import collections, csv, glob, json, os, sys
from manifest import LOW_TEXT, MANIFESTS, incomplete_reason

d = sys.argv[1]
fix = json.load(open('url_fix.json')) if os.path.exists('url_fix.json') else {}
summary, audit = {}, {}
for name, (path, pre) in MANIFESTS.items():
    if not os.path.exists(path): continue
    recs = {}
    for f in sorted(glob.glob(f'{d}/{pre}results_*.jsonl')):
        if name == 'plats' and os.path.basename(f).startswith('extra_'): continue
        for l in open(f):
            try: r = json.loads(l)
            except Exception: continue
            recs[r['seq']] = r
    total = sum(1 for _ in csv.DictReader(open(path)))
    out = 'results.jsonl' if name == 'plats' else 'extra_results.jsonl'
    with open(out, 'w') as fh:
        for k in sorted(recs): fh.write(json.dumps(recs[k]) + '\n')
    st = collections.Counter(r['status'] for r in recs.values())
    summary[name] = dict(total=total, screened=len(recs), status=st,
                         shows_easements=sum(1 for r in recs.values() if r.get('shows_easements')),
                         shows_utility_easement=sum(1 for r in recs.values() if r.get('shows_utility_easement')),
                         by_type_with_ue=collections.Counter(r['type'] for r in recs.values() if r.get('shows_utility_easement')).most_common(15))
    why = {s: incomplete_reason(r, fix if name == 'plats' else None) for s, r in recs.items()}
    reasons = collections.Counter(w or 'complete' for w in why.values())
    low = [dict(seq=s, image=r['image'], type=r['type'], folder=r['folder'], chars=r.get('chars', 0), url=r['url'])
           for s, r in sorted(recs.items()) if why[s] is None and r.get('chars', 0) < LOW_TEXT]
    audit[name] = dict(total=total, screened=len(recs), unscreened=total - len(recs), complete=reasons.pop('complete', 0),
                       incomplete=dict(reasons.most_common()),
                       all_complete=(len(recs) == total and not reasons),
                       still_failing=[dict(seq=s, image=r['image'], folder=r['folder'], url=r['url']) for s, r in sorted(recs.items())
                                      if why[s] in ('download_failed', 'unreadable') and (r.get('v') or 0) >= 3],
                       degraded=[dict(seq=s, image=r['image'], page_status=r.get('page_status')) for s, r in sorted(recs.items())
                                 if (why[s] or '').startswith('pages_degraded')],
                       low_or_no_text_count=len(low))
    json.dump(low, open(f'{pre}low_text.json', 'w'), indent=0)
# legacy top-level keys kept for existing readers (make_queue.py reads the release files, not this)
p = summary.get('plats', {})
summary.update(total_plats=p.get('total'), screened=p.get('screened'), status=p.get('status'),
               shows_easements=p.get('shows_easements'), shows_utility_easement=p.get('shows_utility_easement'),
               by_type_with_ue=p.get('by_type_with_ue'))
json.dump(summary, open('summary.json', 'w'), indent=1)
json.dump(audit, open('completeness.json', 'w'), indent=1)
for k, a in audit.items():
    print(k, {x: a[x] for x in ('total', 'screened', 'complete', 'incomplete', 'all_complete', 'low_or_no_text_count')},
          'still failing', len(a['still_failing']), 'degraded', len(a['degraded']))
