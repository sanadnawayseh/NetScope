# Screenshot gallery and privacy review

Capture date: **7 November 2026**. Source: the UI changes published alongside this gallery, based on `a236aeca464c7241a86b9cee2a385370c081261d`.

## Provenance and scope

All 13 images are real browser captures of NetScope’s current HTML/CSS/JavaScript and API rendering using **simulated data in a new disposable SQLite database**. They are demonstrations, not live network measurements or evidence of manual acceptance. All displayed addresses are loopback (`127.0.0.1`–`127.0.0.6`); generic device labels begin with “Demo.” `example.com` is a reserved documentation-domain input and was not resolved.

The fixture contains four monitored devices and 1,385 generated observations spanning about twelve hours, including exact/bounded RTT, partial loss, independent TCP failures, collection errors, and a twenty-minute gap. The unchanged incident evaluator produced one resolved TCP incident and one active incident; the latter was acknowledged with a clearly simulated note. Two engine-generated notification events were selected for this demo fixture, with mixed read state; this is fixture setup, not a product retention policy. Three discovery inventory rows were seeded without probing.

The server ran separately on loopback, without the automatic monitor. All POST requests, subprocess probes, DNS resolution and outbound connection helpers were blocked. The only demo-specific template change was an invisible simulated-demo browser title. The production UI was otherwise rendered unchanged. The real monitoring database and private inventory were not opened or checksummed.

PDF export used the real read-only report endpoint and browser download flow over simulated data. The capture shows download-started status, not visually accepted PDF pages or a verified Windows download dialog. DNS, traceroute, discovery and enrollment were not executed for these captures; the confirmation dialog was cancelled.

## Capture and review method

- One browser session and the same requested **1440 × 1080** desktop viewport throughout. Content crops exclude browser chrome, accounts and the desktop. Actual output dimensions appear below; cropped/native output sizes do not establish responsive acceptance.
- Browser JPEGs were decoded to RGB PNG without resizing, retouching, redaction or invented chart values. Pixel equality was checked against each decoded capture. Final PNG chunks are only IHDR/IDAT/IEND: no EXIF, XMP, text chunks or embedded paths.
- Every final PNG was opened individually and checked for readable text, whole component edges, settled data, accurate descriptions and privacy. Early crops with edge shifts, partial scroll rows or a loading label were rejected and regenerated. The hero intentionally duplicates the dashboard.
- Only generic labels, loopback addresses, documentation input and generated timestamps appear. No usernames, personal hostnames, real LAN inventory, credentials, emails or filesystem paths were found.
- The fixed fixture naturally ages during capture. Stale labels are retained honestly; stored outcomes do not imply current reachability. Range counts can decrease as the moving time window advances.
- Disposable databases, logs, fixture scripts, raw JPEGs, downloaded reports and review artifacts remain under ignored `tmp/`; only these reviewed PNGs and documentation are published.

## Per-image review

**PASS** means the image passed this visual/privacy review. It is not a security certification, real-network test or manual acceptance result.

| File | Feature shown | Size | Review |
| --- | --- | --- | --- |
| [00-readme-hero.png](00-readme-hero.png) | README hero; identical to the reviewed dashboard capture. | 1440 × 683 | PASS |
| [01-dashboard-overview.png](01-dashboard-overview.png) | Four generic loopback devices; blue navigation/actions, independent ICMP/TCP, unknown results and truthful stale timestamps. | 1440 × 683 | PASS |
| [02-device-history.png](02-device-history.png) | Six-hour RTT/loss charts, bounded RTT and real fixture gaps; quiet ICMP/TCP observation tracks and the matching displayed/range cycle counter. | 1149 × 1180 | PASS |
| [03-incidents.png](03-incidents.png) | All-state filters with one acknowledged active TCP incident and one resolved sampled recovery. | 1145 × 530 | PASS |
| [04-incident-detail.png](04-incident-detail.png) | Resolved TCP lifecycle, original trigger thresholds and verified sampled recovery; no acknowledgement note is claimed for this resolved fixture. | 1095 × 166 | PASS |
| [05-notifications.png](05-notifications.png) | Two engine-generated demo events, one Read and one Unread; incident links and read controls, with complete visible rows. | 1145 × 510 | PASS |
| [06-discovery-inventory.png](06-discovery-inventory.png) | Three seeded responding/unresponsive/unknown loopback observations, deliberate enrollment control, and Start discovery; no persistent checkbox or scan. | 1145 × 654 | PASS |
| [07-network-map.png](07-network-map.png) | Six unconnected stored addresses; selected discovery-only unknown/stale observation, TCP not checked, and no invented links. | 1145 × 774 | PASS |
| [08-analytics-overview.png](08-analytics-overview.png) | Exact RTT statistics/distributions, packet loss, separate sampled availability, preceding-window trends and incident statistics. | 1145 × 2050 | PASS |
| [09-device-comparison.png](09-device-comparison.png) | Two-device six-hour RTT/loss comparison, separate ICMP/TCP tracks, unknown checks and a collection gap. | 1145 × 884 | PASS |
| [10-reporting-export.png](10-reporting-export.png) | PDF export controls and actual simulated-data download-started status; settled comparison context, no loading state. | 1145 × 650 | PASS |
| [11-diagnostics.png](11-diagnostics.png) | DNS Lookup and direct Run traceroute controls with reserved example.com/loopback inputs; neither diagnostic was executed. | 1145 × 335 | PASS |
| [12-discovery-confirmation.png](12-discovery-confirmation.png) | Native per-scan confirmation for 127.0.0.0/29: six usable hosts, exact address range, Cancel and Confirm & Scan; cancelled without a scan. | 1425 × 866 | PASS |

## Limitations

- Screenshots establish rendered demo appearance only. Real Windows startup/locale behavior, newly changed authorization flows against an authorized real network, full responsive/assistive-technology acceptance and final PDF/download acceptance remain separate manual gates.
- Sampled availability is not continuous uptime. Unknown, bounded and missing samples are not fabricated successes or failures; map connections remain unverified.
- The complete automated run used temporary databases and mocked operations: **269 Python passed, one live-loopback case skipped; 118 frontend passed**. No unmocked operation was attempted. See the [release record](../RELEASE_CHECKLIST.md#ui-and-screenshot-refresh--7-november-2026) and [manual acceptance limits](../FINAL_ACCEPTANCE.md).
- All twelve previous image files were outdated after the palette, navigation and interaction changes and were replaced under their existing names. One confirmation-dialog image was added; no genuinely current legacy image was discarded.

## Reviewed SHA256 checksums

- `00-readme-hero.png`: `7b22ec597035cd7b86a864d18254570404fc06999df013ab67310240e75c3d9f`
- `01-dashboard-overview.png`: `7b22ec597035cd7b86a864d18254570404fc06999df013ab67310240e75c3d9f`
- `02-device-history.png`: `d47c2da5cc568a5795f74096cc4ba1d0a8da5be1c461abbf293d51367de23580`
- `03-incidents.png`: `fa9875dada1e236259192fbc48b24611c8613aee302e4cd13892d2a90c8c0974`
- `04-incident-detail.png`: `afa58980d3b1f6e697d99cd5cd3d6ea8b9730ef15c75152392d6899964f44413`
- `05-notifications.png`: `505877a8781661d9c1655bd7f7964e550187e3ca9daccdf40d7768060722892a`
- `06-discovery-inventory.png`: `96dbd2723cdb4e0bdc9dd5867e933e06814b78bad7eb1fd92f94175d22f0b949`
- `07-network-map.png`: `ad04d2ff82f4e481e6697b6efb53a1bc7bc3d09603f04101de7ebdcaca7a8e13`
- `08-analytics-overview.png`: `9791e77bc27c3311d4941770488818f1486fa71b9c363de31c9694ca4fd35c10`
- `09-device-comparison.png`: `aa7dd6ae3b95e966e4c41346d852061f4b42fdb3bf2ed1f9946bc7fc688e9e78`
- `10-reporting-export.png`: `4b45a3486d832257ccea499a0ab3a0488bd97e1980063074b5475abd000c168a`
- `11-diagnostics.png`: `d4b2f61f757042f853d9205e8bc747884ff687bca9c1db922126358a12a9f09d`
- `12-discovery-confirmation.png`: `2e8463477e7b9427323aa08355d6606723c7228c331082c61ab22fcb42d6bc9e`
