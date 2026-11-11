# Benton County plat screening

Automated first-pass screening of every plat in the Benton County, Arkansas plat repository
(`gis.bentoncountyar.gov/plats/...`, indexed by the county's Plat Scans layer) for easements.

For each plat, the screening:
- downloads the PDF (paced, so the county server isn't hammered);
- runs OCR on each page at screening resolution;
- records whether easements are shown, any width callouts it can read, key phrases (dedications, use restrictions, private streets, releases), and the raw text.

This is triage only. The widths and use rights that go into the master dataset are read and verified by hand, with evidence.

- `plats.csv`: the county-index manifest. Final and corrected plats come first, then other plats, splits and adjustments, then surveys.
- `extra.csv`: every plat-like file on the county server (PDF/TIF/JPG/PNG) that the index does not reference: State Filed
  Plats, AHTD highway ROW plans, All_New_Plats, extra RURAL and city files, ECMP, Corner Scans (last). Byte-identical
  duplicates of indexed files are skipped (`extra_duplicates.json.gz`). Rows keep a fixed seq/chunk.
- `url_fix.json`: repaired URLs for index entries that 404 (exact name, punctuation-only, or same instrument number with a
  near-identical name, unambiguous only). `url_unresolved.json`: the rest, for manual follow-up; those server files are
  still screened via `extra.csv`.
- `results.jsonl` / `extra_results.jsonl` / `summary.json`: merged results, updated after each run.
- `completeness.json`: the audit gate. Complete = downloaded and opened, every page OCR'd at 200 dpi (huge sheets in
  tiles) with no timeout or error, no page cap, and a 300-dpi second pass for low-text files. Older records that don't
  meet this are re-screened once automatically (`redo.py`). Also lists files still failing, pages still degraded after
  the long retry, and `low_text.json` / `extra_low_text.json` (little or no text even at 300 dpi: candidates for visual reading).
- Release `screening-v2`: per-chunk results and OCR text (`results_<c>`, `text_<c>`, `extra_results_<c>`, `extra_text_<c>`).
- **Refresh manifests** workflow (Saturdays): re-crawls the server (`crawl.py`), pulls the live index (`index.py`), repairs URLs
  (`fix_urls.py`) and appends new unindexed files (`build_extra.py`).

Run it from **Actions → Screen all plats → Run workflow**. Chunks that are already done are skipped, so the workflow can be re-run to finish any that hit the time limit.
