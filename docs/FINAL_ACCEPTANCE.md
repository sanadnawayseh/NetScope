# Final acceptance audit — 23 October 2026

Audited baseline: `179d9d52658e0caed94a08a3a6cb79dc5f820337` on `main`.
Verdict: **NO-GO for declaring final acceptance complete**. Automated behavior is strong enough for a portfolio release candidate, but manual Windows/browser acceptance and release decisions remain open. No critical vulnerability was demonstrated; this review is not a penetration test or dependency vulnerability certification.

## Feature acceptance matrix

PASS below means source inspection and the executed automated tests support the stated scope; it does not certify every operating environment.

| Area | Result | Evidence / remaining boundary |
| --- | --- | --- |
| Schema 0–5, partial additive legacy schemas, repeat initialization | PASS | Migration compatibility, unsupported schema rejection, rollback, foreign keys and WAL tests; fresh/v0 real-launcher simulation. |
| Persistence, restart, contention, invalid writes | PASS | Temporary database integration tests preserve rows and exercise rollback, busy failures and subsequent writes. |
| Concurrent monitoring, scheduling, worker bound, overlap protection | PASS | Coordinator tests and simulated 20-device benchmark; four-worker maximum observed. |
| Shutdown, cancellation, isolation and logging | PASS | Queue cancellation/draining, lifecycle races, terminal jobs, rotation, severity and redaction tests. Running Python threads cannot be forcibly killed. |
| Device creation, independent ICMP/TCP, manual async jobs | PASS | API tests, DOM tests and actual browser with simulated probes; manual job reached completed 1/1, 100%, zero failed. |
| Charts, unknown data, history windows, comparison, polling | PASS | Timestamp/gap, sparse data, eligibility, ordering and automatic-update regressions. |
| Full desktop/mobile/touch/screen-reader acceptance | NOT VERIFIED | Limited in-app browser smoke passed. Requested 390px override measured 650px; true phone width was not demonstrated. |
| Incident thresholds, deduplication, flapping, ordering and recovery | PASS | Deterministic integration tests, including restart, rollback and independent protocol recovery. |
| Acknowledgement, notification events and read state | PASS | OPEN → ACKNOWLEDGED → RESOLVED and idempotence tests. Simulated outage/recovery creates exactly one opened and one resolved event, unchanged after re-evaluation/restart. |
| OS desktop notification delivery | NOT VERIFIED | Permission/denial/deduplication logic passes simulated frontend tests; no browser permission requested or OS delivery claimed. |
| Discovery validation, limits, persistence, enrollment, cancellation | PASS | Mocked discovery tests only: no real discovery scan. |
| Map classification, freshness, no invented topology, controls | PASS | DOM tests plus actual browser zoom/reset/arrow/Home actions and stored-data rendering. Mouse drag/touch and assistive technology remain manual. |
| Analytics, percentiles, availability and incident spans | PASS | Statistical, invalid/missing data, preceding-window, deleted-device and query-budget tests. |
| CSV/PDF content, escaping, bounds and headers | PASS | CSV assertions, pypdf parsing, Unicode fallback, private download headers and size/time limits. Actual download dialogs/visual acceptance remain manual. |
| Clean locked installation and launcher integration | PASS | Offline reconstructed wheels in a new isolated venv; runtime/dev install, pip check, fresh/v0 launcher, simulated persistence and graceful shutdown. |
| Fresh PyPI installation, artifact provenance and vulnerability audit | NOT VERIFIED | Exact pins have no artifact hashes; offline repacking cannot validate upstream provenance. |
| README/configuration/architecture/reference links | PASS | Source review and 22 resolving local links. Mermaid source reviewed; renderer not rerun in this audit. |
| Current screenshots and licensing | NOT VERIFIED | Screenshots are explicitly legacy; no repository license has been chosen. |

## Exact executed checks

- `python -B tools/verify_install.py`: passed runtime-only installation of ten locked distributions, development installation of twelve, isolated environment, `pip check`, fresh/v0 launcher simulations and **260 Python tests in 49.246s**, zero failures/errors. Launcher smoke blocks subprocess probes and HTTP serving; the original full-suite loopback regression still runs.
- Initial `node --test --test-isolation=none tests/test_dashboard.cjs`: **91 passed**, zero failed/cancelled/skipped, **1706.7075ms**.
- New numeric regression reproduced a defect: historical focused suite **9 passed / 1 failed**, **1.885s**. This was an intentional failing regression run, not the final result.
- After the fix, `python -B -m unittest discover -s tests -p test_historical_analytics.py -v`: **10 passed**, **2.381s**.
- Final full `python -B -m unittest discover -s tests -v`: **261 passed**, **51.671s**, zero failures/errors, using a temporary default DB/log environment and disposable test fixtures.
- Final full frontend suite: **91 passed**, zero failed/cancelled/skipped, **3835.9818ms**.
- Syntax: **38 Python files** parsed, **six frontend modules plus the frontend test file** passed `node --check`. Changed Python files parsed again after the correction.
- Configured-environment `pip check`, `git diff --check`, 22 local documentation links and tracked-text secret-pattern scan passed. Pattern matching is not proof that every possible secret is absent.
- Bounds: Python discovery 120s; frontend 30s; each benchmark 90s; install subprocesses 90s; launcher smoke 35s. No timeout occurred. Browser fixture had a 240s automatic shutdown.

## Benchmarks

All use simulated measurements and temporary databases. Three repetitions per workload; analytics/report runs overlapped the install/test work, so timings are noisy rather than a controlled regression comparison.

| Workload | Results |
| --- | --- |
| Monitoring, 20 uniform simulated devices | One worker median 2.250920s; four workers 1.223479s. All 20 results persisted per run; zero failures; maximum active 1 or 4 as configured. |
| Monitoring, mixed simulated delays | One worker median 2.367607s; four workers 1.153930s. Same correctness/bounds. |
| Analytics, 50,000 measurements / 2,000 incidents / four devices | 4.028268s, 3.669257s, 5.965320s; median 4.028268s; 12,966-byte responses. Query plan uses `idx_results_observed`. |
| CSV, 10,000 measurements / 13 incidents / four devices | 2.908406s, 3.497129s, 2.929444s; median 2.929444s; 2,140,186 bytes per response. |
| PDF, same report fixture | 1.703908s, 1.117619s, 0.964183s; median 1.117619s; 49,908–49,911 bytes. |

The recorded earlier analytics median was 3.592s and CSV 2.345s. These runs are slower, but uncontrolled concurrent CPU/I/O activity prevents attributing the difference to a code regression. No budget/correctness failure was observed. Incident aggregation still scans a bounded projected incident set; exact percentiles sort bounded arrays. Ordinary measurement/incident/notification storage has no retention, so long-lived performance remains an operational limitation. Benchmarks preceded the small online-mean correction; final integration tests cover that correction.

## Defect and correction

`historical_analytics.py` accumulated finite nonnegative RTTs in a floating-point sum. Two `1e308` samples overflowed the sum and returned nonstandard JSON `Infinity`; strict browser JSON consumers can fail. Replaced that sum/division with an online mean, preserving eligibility, row counts, data and API shape. Added `test_large_finite_rtt_mean_remains_finite_json`, which rejects nonfinite JSON and checks the correct finite mean. The input is deliberately extreme but was accepted by existing validation. No schema or stored data changed.

## Security and code quality

- Loopback peer/Host, exact supplied Origin, fetch-site and body limits are enforced before routes. CSP, framing, no-sniff and API no-store safeguards are present; reports add private attachment/sandbox headers. These are local request boundaries, not authentication against other local programs.
- Targets accept private/loopback literal IPv4 with validated ports. Discovery requires explicit consent, canonical bounded CIDRs and limits; traceroute has separate authorization. Subprocesses use argument arrays, `shell=False` and deadlines. No shell execution of user input was found in reviewed runtime paths.
- Request SQL values are parameterized. Dynamic identifiers/expressions come from internal allowlists/constants. History points, analytics rows, incident projections, discovery retention and report output are bounded; cooperative time limits do not guarantee hard preemption of every CPU/library operation.
- SQLite connections close in context managers; coordinator locks use cleanup paths; report admission releases in `finally`. Measurements commit before incident evaluation; trigger events commit with incident transitions, and failed secondary evaluation keeps cursors retryable.
- Broad exception handlers remain at probe/background/secondary-service boundaries and generally preserve unknown states, safe errors or terminal job states. This protects monitoring, but allowlisted logs omit tracebacks/messages and can make root-cause diagnosis harder. No new logging exposure was introduced.
- `app.py` remains a large orchestration module; compressed one-line Python/CSS and global frontend state raise maintenance cost. These are refactoring opportunities, not justification for a release-time rewrite. Shared classification/history/analytics paths avoid a second monitoring or incident engine.
- Locks/jobs/report admission are process-local. Multiple processes, remote exposure and a durable task queue are unsupported. DNS OS resolver work cannot be killed safely; monitoring shutdown drains running bounded work. Desktop delivery remains best effort with an open page.
- No critical/high exploit was demonstrated. No external penetration test, fresh upstream artifact verification or vulnerability feed audit was performed. Sensitive user data remains private; the real DB SHA256 remained `fe7d2ffab06eb0f31dcac44fdd6f78a44f5acf7033bf228edab550f7394706b0`.

## Manual Windows acceptance — owner actions

Use a **new** disposable directory and database; do not point this exercise at the real database. The following commands start actual monitoring of the default loopback target. Run them only when ready to authorize that local test:

```powershell
$acceptanceRoot = Join-Path $env:TEMP ('NetScope-acceptance-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $acceptanceRoot
$env:NETSCOPE_DB = Join-Path $acceptanceRoot 'acceptance.db'
$env:NETSCOPE_LOG_DIR = Join-Path $acceptanceRoot 'logs'
.\.venv\Scripts\python.exe app.py
```

1. Open `http://127.0.0.1:5000`. Verify automatic updates, manual progress, a second click/busy behavior and independent ICMP/TCP. Add only your own explicitly authorized device/port. Compare two devices over history ranges; check missing/bounded RTT labels.
2. For a controlled TCP service on your computer, use a second terminal: `.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1`. Add `127.0.0.1:8765`, verify success, then Ctrl+C **that test service**. Observe at least three qualifying checks for TCP incident opening; ICMP success must not resolve it. Acknowledge it, restart the same test service, and observe at least two qualifying recovery checks. Default thresholds are three bad/two good, with delay after each batch; preexisting configuration or unknown checks can alter the wait.
3. Verify one opened and one resolved notification, unread/read behavior, sampled span wording and persistence after normal NetScope Ctrl+C/restart with the same disposable DB. No exact continuous outage duration should be claimed.
4. Explicitly enable desktop alerts in the browser and verify its permission/denial path and OS delivery; test reload and repeated polls for duplicates. Test with the page open; do not expect closed-browser delivery.
5. Choose an exact private CIDR you own or are authorized to test, with at most 32 usable hosts. Enter it yourself and confirm fresh consent. Check progress/cancellation, stored inventory and deliberate enrollment. Do not use a guessed/shared range.
6. On desktop and a real narrow/mobile viewport, test map selection, filters, mouse drag, wheel/buttons/reset, Tab/Enter/Space/arrows/Home and visible focus. Confirm stale/unknown labels and no topology edges. Check form/report/incident controls at 390px and 320px, zoom and screen-reader navigation.
7. Download CSV and PDF, check filenames/headers, empty and larger windows, Unicode/long names, pagination, N/A and formulas-as-text in a spreadsheet. Inspect PDF pages visually. Keep these exports private.
8. Capture genuine current incident/notification, map, analytics and export screenshots using the privacy checklist. Use simulated/disposable loopback records and review names, addresses, tabs and metadata; do not publish personal exports.
9. Stop both test processes normally. Confirm persistence on restart. To return a later PowerShell session to defaults, remove only the session variables: `Remove-Item Env:NETSCOPE_DB, Env:NETSCOPE_LOG_DIR`. Keep or dispose of test artifacts deliberately; these instructions do not delete them.

## Ranked remaining gates

| Priority | Gate |
| --- | --- |
| Critical | None demonstrated in the tested local deployment scope. |
| High | Complete and record normal Windows/browser end-to-end acceptance before calling the release fully accepted. Remote/public service deployment remains unsupported. |
| Medium | Choose a repository license; verify a fresh upstream lock install and dependency provenance/security; verify true phone-width/assistive-technology behavior and actual download/notification workflows. |
| Low | Replace explicitly legacy screenshots with reviewed genuine current captures; verify Mermaid rendering in the publication target; track long-history performance and improve readability in future focused work. |

No commit or push was performed. Only the numeric fix, its regression test and this audit document were added/modified. Publishing a clearly labeled local portfolio release candidate is a separate owner decision; a claim of 100% final verification is not supported yet.
