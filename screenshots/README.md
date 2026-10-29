# NetScope screenshots

The current UI screenshot set and per-image privacy review are in [docs/screenshots](../docs/screenshots/README.md). These browser captures use an explicitly disclosed simulated, disposable loopback demo. Earlier genuine 15 October 2026 loopback captures (`dashboard/overview.png` and `history/measurements.png`) predate the current interface and have been excluded from the current Git tree; local copies are ignored. They remain in historical commits and were visually reviewed as loopback-only, with no personal inventory shown. Their original capture provenance is documented in earlier revisions; this audit does not independently recreate that capture session.

Capture from the actual application using the checklist below. Simulated fixtures must be clearly disclosed and must never be presented as real monitoring or discovery results.

## Suggested genuine captures

| Folder | Suggested image | What to show |
| --- | --- | --- |
| `dashboard/` | `overview.png` | Compact device table with independently labeled ICMP and TCP columns. |
| `history/` | `measurements.png` | Selected device heading, real RTT and packet-loss history. |
| `history/` | `availability.png` | ICMP/TCP check availability with sample counts and observation windows. |
| `diagnostics/` | `dns.png` | Actual system resolver lookup and explanatory result. |
| `diagnostics/` | `traceroute.png` | Actual authorized loopback trace with reported hop timings. |

## Capture and privacy checklist

1. Run the actual application locally. For publishable captures, use a separate disposable database with only loopback targets rather than your personal monitoring database. Stop that instance before switching databases. `NETSCOPE_DB` selects the database file; never overwrite the original.
2. Let real monitoring create measurements. Empty history, N/A, `<1 ms`, refused connections, or missing DNS address families are legitimate outcomes. For real-measurement captures, do not manufacture successful results or replace unavailable values. Keep simulated illustration captures separate and label their provenance explicitly.
3. Capture the browser using Windows Snipping Tool. Crop browser/profile/desktop details if needed; leave measurement text accurate.
4. Review device names, non-loopback IPs, DNS names, timestamps, hop addresses, usernames, paths, browser tabs, notifications, and image metadata. Remove sensitive content by recapturing a sanitized setup, rather than altering network measurements.
5. Keep unreviewed originals in `screenshots/raw/` (ignored by Git) or outside this repository. Move only reviewed images into the category folders.
6. Add relative image links and descriptive alt text to the root README only when the image files exist. Review Git's staged filenames and image contents before publication.

The current runtime database is private and must not be committed to support a screenshot. Capture files should document real behavior, not serve as evidence that every platform or network was tested.
