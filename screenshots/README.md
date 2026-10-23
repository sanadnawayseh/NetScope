# NetScope screenshots

`dashboard/overview.png` and `history/measurements.png` are actual browser captures from 15 October 2026. They show a separate disposable SQLite preview with loopback targets only, including real five-request ICMP batches and TCP checks. Exact RTT is intentionally unavailable when ping reports `<1 ms`; the blank exact-latency plot is accurate. All displayed results came through the existing backend. No measurements were seeded or edited for these images.

The preview database is ignored and is not included. Personal monitoring devices/history were not used. The remaining `.gitkeep` files retain the category structure. Capture additional images using the checklist below; do not substitute generated images or mock monitoring values.

These captures predate the incident/notification dashboard, authorized discovery, Network Map, advanced analytics and report export controls. The current README identifies them as legacy views. Before release, capture those five current workflows manually from an isolated authorized setup, including permission/empty/error states where useful. No generated or mocked UI image is added as a genuine screenshot. See the [release checklist](../docs/RELEASE_CHECKLIST.md).

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
2. Let real monitoring create measurements. Empty history, N/A, `<1 ms`, refused connections, or missing DNS address families are legitimate outcomes. Do not manufacture successful results or replace unavailable values.
3. Capture the browser using Windows Snipping Tool. Crop browser/profile/desktop details if needed; leave measurement text accurate.
4. Review device names, non-loopback IPs, DNS names, timestamps, hop addresses, usernames, paths, browser tabs, notifications, and image metadata. Remove sensitive content by recapturing a sanitized setup, rather than altering network measurements.
5. Keep unreviewed originals in `screenshots/raw/` (ignored by Git) or outside this repository. Move only reviewed images into the category folders.
6. Add relative image links and descriptive alt text to the root README only when the image files exist. Review Git's staged filenames and image contents before publication.

The current runtime database is private and must not be committed to support a screenshot. Capture files should document real behavior, not serve as evidence that every platform or network was tested.
