"""Merge per-chunk screening results into results.jsonl + summary.json."""
import json, sys, glob, collections, csv
recs = {}
for f in glob.glob(sys.argv[1] + '/results_*.jsonl'):
    for l in open(f):
        try: r = json.loads(l)
        except Exception: continue
        recs[r['seq']] = r
total = sum(1 for _ in csv.DictReader(open('plats.csv')))
with open('results.jsonl', 'w') as f:
    for k in sorted(recs): f.write(json.dumps(recs[k]) + '\n')
st = collections.Counter(r['status'] for r in recs.values())
ue = sum(1 for r in recs.values() if r.get('shows_utility_easement'))
es = sum(1 for r in recs.values() if r.get('shows_easements'))
json.dump(dict(total_plats=total, screened=len(recs), status=st, shows_easements=es, shows_utility_easement=ue,
               by_type_with_ue=collections.Counter(r['type'] for r in recs.values() if r.get('shows_utility_easement')).most_common(15)),
          open('summary.json', 'w'), indent=1)
print(open('summary.json').read())
