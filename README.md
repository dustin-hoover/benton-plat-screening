# Benton County plat screening

Automated first-pass screening of every plat in the Benton County, Arkansas plat repository
(`gis.bentoncountyar.gov/plats/...`, indexed by the county's Plat Scans layer) for easements.

For each plat, the screening:
- downloads the PDF (paced, so the county server isn't hammered);
- runs OCR on each page at screening resolution;
- records whether easements are shown, any width callouts it can read, key phrases (dedications, use restrictions, private streets, releases), and the raw text.

This is triage only. The widths and use rights that go into the master dataset are read and verified by hand, with evidence.

- `plats.csv`: the manifest. Final and corrected plats come first, then other plats, splits and adjustments, then surveys.
- `results.jsonl` / `summary.json`: merged results, updated after each run.
- Release `screening-data`: per-chunk results and OCR text.

Run it from **Actions → Screen all plats → Run workflow**. Chunks that are already done are skipped, so the workflow can be re-run to finish any that hit the time limit.
