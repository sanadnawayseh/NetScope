# Screenshot gallery and privacy review

Capture date: 29 October 2026. Source checkpoint: `5b3db02ff8d3493b3729985951cfeb7e211c4f8d`.

## Provenance and scope

These are browser captures of the current NetScope HTML/CSS/JavaScript and real API/analytics/report rendering, using **simulated demo data in a newly created disposable SQLite database**. They are not screenshots of the owner's real inventory or evidence of real network measurements/discovery. All device addresses are loopback (`127.0.0.1`–`127.0.0.4`); `example.com` is a reserved documentation domain entered into the DNS form without resolving it.

The fixture inserted 525 simulated observations for three targets, including exact/bounded RTT, loss, TCP failures, a collection gap, and recovery. The existing persistence/incident engine produced the incident transitions and notification events. Demo acknowledgement timestamps were made consistent with the simulated historical lifecycle; read state was deliberately mixed. Four discovery inventory rows were seeded, with no scan run. The demo server ran separately on loopback port 5055, without the background monitor; all non-GET actions were blocked. Its template response added the top DEMO DATA badge and replaced example LAN input placeholders with loopback examples. Production source files were not changed.

A PDF was generated from this demo data through the real export UI; the image shows the app's download-started state, not a reviewed PDF page or proof of a Windows download dialog. DNS and traceroute are shown before execution. No external DNS, traceroute, discovery, router or LAN access occurred. No uptime was fabricated: availability values are the app's sampled-check statistics.

## Capture and review method

- Same browser session, current dark theme and a requested 1440 × 1080 viewport throughout. Content-only region captures exclude browser chrome, account controls, bookmarks and desktop. Actual output sizes differ because panels were cropped to their content; full-viewport capture behavior is not evidence of responsive-layout acceptance.
- Browser JPEG captures were decoded and saved as RGB PNGs without resizing, retouching or invented chart values. Final PNGs contain only IHDR/IDAT/IEND chunks: no EXIF, XMP, textual metadata or embedded paths.
- Each final image was opened and reviewed individually. Visible text was checked for personal hostnames, real LAN addresses, usernames, Windows account names, filesystem paths, bookmarks, email addresses and credentials. Rendered-text checks complemented visual review. Only generic labels, loopback addresses, documentation domain input and demo timestamps are present.
- No pixel redaction was needed. The incident capture was regenerated after correcting a synthetic acknowledgement timestamp and resetting list scroll. Notifications were regenerated so both Read and Unread appear in the visible portion. Export framing was tightened to avoid a partial unrelated line. The hero is an intentional copy of the dashboard image.
- The real database checksum remained unchanged. Disposable database, fixture script, raw captures, text snapshots and test-only artifacts remain under ignored `tmp/`; they are excluded from the public screenshot set. Existing legacy screenshots remain ignored.

## Per-image review

PASS means no personal/sensitive content was found in this review; it is not a general application security or manual acceptance certification.

| File | Feature shown | Size | Privacy review |
| --- | --- | --- | --- |
| [00-readme-hero.png](00-readme-hero.png) | README hero; same reviewed capture as dashboard | 1425 × 922 | PASS |
| [01-dashboard-overview.png](01-dashboard-overview.png) | Three loopback devices; independent ICMP/TCP, summary RTT, loss and stale timestamps | 1425 × 922 | PASS |
| [02-device-history.png](02-device-history.png) | Six-hour exact RTT/loss charts, bounded-data and collection gaps, sampled availability timeline | 1207 × 1092 | PASS |
| [03-incidents.png](03-incidents.png) | Two resolved incidents and one acknowledged active incident with filters | 1207 × 768 | PASS |
| [04-incident-detail.png](04-incident-detail.png) | Resolved lifecycle, acknowledgement note, trigger thresholds and verified sampled recovery | 1174 × 220 | PASS |
| [05-notifications.png](05-notifications.png) | Persistent in-app opened/resolved events, Read/Unread states and incident links | 1207 × 615 | PASS |
| [06-discovery-inventory.png](06-discovery-inventory.png) | Simulated responding/unresponsive/unknown loopback inventory; consent unchecked; no scan | 1207 × 764 | PASS |
| [07-network-map.png](07-network-map.png) | Unconnected stored inventory, selection details, stale/unknown states and no invented links | 1207 × 776 | PASS |
| [08-analytics-overview.png](08-analytics-overview.png) | Mean/median/p95/p99, loss distribution, sampled availability and incident statistics | 1222 × 1937 | PASS |
| [09-device-comparison.png](09-device-comparison.png) | Two-device RTT/loss/availability comparison and gaps | 1207 × 688 | PASS |
| [10-reporting-export.png](10-reporting-export.png) | PDF export controls and real demo-data download-started status | 1222 × 483 | PASS |
| [11-diagnostics.png](11-diagnostics.png) | Safe example.com/loopback inputs; DNS/traceroute not executed | 1222 × 745 | PASS |

## Limitations

- All measurements and inventory are simulated; a successful rendered demo does not complete the pending real-browser/manual acceptance matrix.
- Map freshness is truthful: fixed demo observations aged into stale state during capture. No topology edges are claimed.
- Preceding-window trends remain N/A where the fixture lacks five eligible samples in each window. Unknown, bounded and missing RTT remain gaps rather than invented points.
- Panels with built-in scrolling retain that behavior; the notification image intentionally shows a viewport of the event list. Full records remain in the disposable database.
- Diagnostics results and visual PDF pagination are not captured. No real discovery consent/progress or scan result is claimed.

## Reviewed image checksums (SHA256)

- `00-readme-hero.png`: `2f549ff37693828a808652b14221b6b76b78cc8a101135dd00b085e2112ab702`
- `01-dashboard-overview.png`: `2f549ff37693828a808652b14221b6b76b78cc8a101135dd00b085e2112ab702`
- `02-device-history.png`: `a4bb2d088f028de4481c5516d2b00347da40b54b32f9c022812ff36bff44198a`
- `03-incidents.png`: `e67e214fef82f810f16ad1d7ad6a0dd47460e20f582326b606b1d71f9872c5e9`
- `04-incident-detail.png`: `967572778598475627a0941d415db00ea5b433ac010bd4e3355e516fbb581133`
- `05-notifications.png`: `a944f23e0dbdeb69a214de8663bcbb3a89b2b73696e58228fcd419afb4381cc5`
- `06-discovery-inventory.png`: `6b0efabb89bc1eca3c308bd74c92a17c18af81fcdbbd9d87856155969a09b89c`
- `07-network-map.png`: `d6ff443ebd9ba68fe1cfb5b08c9412f81bdcb897e6f81fa6eaafdec6e4a2c64d`
- `08-analytics-overview.png`: `edfc4ec6c8571d89dbd579ada35fdc0735d3135eb37f7a4ba8398d9e6fe7ad20`
- `09-device-comparison.png`: `e662e67ed422600190eef2517d74b385fd85d369b841795694dd52164bf32d1b`
- `10-reporting-export.png`: `fb99fbe2d76c9ee27c1b15c14aadc1473f955127170320372f58923de904c934`
- `11-diagnostics.png`: `dda4f4b23b2ab59b206567a87505c4f51df35161df51abaaaa7a3f82d5d7719c`
