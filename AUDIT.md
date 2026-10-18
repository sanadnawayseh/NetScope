# NetScope final technical audit

Audit date: 14 October 2026. Scope: all application modules, frontend/template/style files, configuration/documentation, existing tests, and a read-only inspection of the live SQLite database. This is a source and behavior audit of a local single-user portfolio application, not certification for public deployment.

## Findings fixed

| Priority | Confirmed issue | Resolution and regression evidence |
| --- | --- | --- |
| High | Monitoring/read endpoints lacked the local peer, Host, and origin boundary already present on diagnostic endpoints. A browser origin/DNS-rebinding path could interact with monitoring or read device records. | A shared `before_request` boundary protects every route. Non-loopback peers, unrelated Hosts, cross-origin requests and cross-site fetches are rejected before work. Boundary tests exercise reads and mutations. |
| High | Deleting and recreating a device during a monitoring batch could reuse its SQLite primary key and attach the old target's result to the new device. | Device writes share the batch lock and return 409 while checks are active. Creation validates/reads the request before acquiring the short database-write lock. No rows or IDs were rewritten. |
| Medium | An outer database failure could terminate the background monitor; manual execution exceptions could still produce “Checks completed.” | Background errors are logged and retried after the interval. Manual checks return completion/failure counts and a safe 503 for execution failures. Completed negative network results still count as completed checks. |
| Medium | Float/boolean ports were coerced to integers, and infinite values could raise an unhandled exception. IPv4 validation also accepted packed bytes and integer addresses outside the advertised input contract. | Strict text-only IPv4 and integer/decimal-text port validation, with 1–65535 bounds. API bodies are bounded to 4096 bytes; DNS/traceroute retain 1024-byte limits. |
| Medium | Device status and historical summaries could read different SQLite snapshots when a batch inserted results between SELECTs. | The target/status/availability read uses one explicit read transaction. A concurrent-writer regression verifies a coherent snapshot without blocking the WAL writer. |
| Medium | Local socket resource/permission failures were classified as network failures and could lower TCP check availability. | Known network error codes remain failures. Local/unclassifiable errors remain unknown and are excluded. Older identifiable local-error rows are reclassified on read without modifying stored history. |
| Medium | Emptying the device list and restarting silently re-created the default device. | Default seeding occurs only when creating the targets table, preserving an intentionally empty list. |
| Medium | A device mutation during an in-flight poll could skip its refresh; history errors could mark the whole backend disconnected and disappear on redraw. Unknown checks looked like confirmed failures. | Poll requests share a promise; mutation refreshes run after stale polls. History loading/errors persist across redraw and stay scoped to history. Unknown results are labeled separately. Three new frontend regressions pass. |
| Low | Malformed ping RTTs could become non-finite JSON values; unexpected command exit codes could still be accepted as measurements. | Non-finite/impossible RTTs and unexpected exits become unavailable statistics, never invented loss/latency values. Windows ping now uses the system executable and explicitly disables the shell. |
| Low | A giant malformed traceroute hop number could raise during integer parsing. | The numbered-hop parser bounds the hop-number token before conversion. Existing hop/output/deadline limits remain enforced. |
| Medium | Windows MIME registry served JavaScript as `text/plain`; enabling `nosniff` exposed the existing incorrect MIME type and stalled the first browser smoke run. | Static JavaScript is explicitly `text/javascript`, with an mtime-versioned URL to bypass old cached responses. A wrong-registry regression and the subsequent successful browser check verify the fix. |
| Hardening | Embedding, stale API caching, oversized bodies, and raw HTML database errors were not consistently addressed. | CSP, frame denial, MIME sniffing prevention, no-store APIs, safe JSON database/HTTP errors, explicit debug/reloader settings, and expanded publication ignore patterns. |

The new audit regressions were run against the previous implementation and reproduced the failures before fixes. No visual redesign, production fake data, new dependencies, schema rewrite, history deletion, Git commit, push, or publication was performed.

## Reviewed behavior retained

- **ICMP:** five requests per cycle; literal loopback/RFC1918 destination validation before execution; argument-list subprocess with a deadline. Reply-derived RTT only. Zero responses produce valid 100% loss only when a complete, consistent batch is available. Command/unparseable/incomplete output remains unknown. Bounds such as `<1 ms` are not converted to exact RTTs.
- **TCP:** independent literal-IP connection checks, bounded connection wait, context-managed socket, separate refusal/timeout/network/unavailable diagnostics. Ping success does not imply TCP service availability.
- **Packet loss:** `(sent − echo responses) / sent × 100`, within a cycle. Windows unreachable messages are not successful echoes. Successful replies alone supply RTT statistics; missing replies never contribute fake zeros.
- **Availability:** successful/valid completed checks within the latest 60 stored records, separate protocol denominators, counts and qualifying observation windows. At least one echo makes an ICMP cycle successful. Legacy/incomplete/unknown measurements are excluded as appropriate; no continuous uptime or outage durations are inferred.
- **SQLite:** bound SQL parameters for request data, foreign keys/cascades, WAL and indexed history, deterministic close/rollback, additive startup migration, and no automatic corruption reset. The original database was inspected via `mode=ro`; `quick_check` returned `ok`, and `foreign_key_check` returned zero violations. Test writes use disposable databases.
- **DNS:** validated/IDNA domain names, absolute-name system resolution, blocked local-only suffixes, withheld non-public results, no connections to returned addresses, bounded response/address counts, one resolver worker and six accepted lookups per minute. DNS cannot add public monitoring targets or choose a resolver/URL/port.
- **Traceroute:** explicit authorization assertion; literal loopback/RFC1918 IPv4 only; system `tracert.exe -d -4` with an argument list and `shell=False`; 1–20 hops, 500 ms waits, up to 35 seconds, one trace and two accepted executions per minute. Reached/incomplete/command-failure states remain separate. A `*` is not proof of a router outage. No automatic traces or SQLite writes.
- **Frontend:** user-controlled text uses `textContent`, not HTML interpolation. Device selection/request-generation guards prevent old history responses from replacing newer selections. Empty/error states, reduced-motion navigation, and exact-RTT versus bounds behavior remain covered.
- **Launch:** supplied `python app.py` path binds `127.0.0.1:5000`, debug is false, and the reloader is explicitly false. Do not use a public bind, tunnel, reverse proxy, debugger, alternate CLI launch, or multiple workers.

## Executed verification

Baseline: 81 Python tests and 27 dashboard tests passed.

Final automated run after code changes:

| Command/check | Actual result |
| --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests -q` | **101 tests ran, all passed** (final recorded run: 7.755 seconds). |
| `node --check static/app.js` | Passed; exit code 0. |
| `node --test --test-isolation=none tests/test_dashboard.cjs` | **30 tests ran, 30 passed, 0 failed, 0 skipped**. |
| `.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` |
| Original SQLite read-only checks | `quick_check`: `ok`; foreign-key violations: 0. |
| Real loopback probe exercised by Python suite | 5 sent, 5 received, 0 unanswered, 0% loss; ICMP succeeded, TCP connected to a temporary listening loopback socket; RTT stayed bounded at `<1 ms`. |

The final browser smoke check used a disposable snapshot of actual SQLite history with automatic monitoring disabled. Router history rendered 60 exact RTT samples. A real system DNS lookup of `example.com` succeeded (IPv4 answers available, no IPv6 answers returned by this system). A real Windows loopback trace reached `127.0.0.1` in one hop with three `<1 ms` bounds. Desktop/mobile diagnostics were inspected with the final CSP/headers, and the browser log contained no errors/warnings. The initial smoke run's MIME failure was corrected before these successful checks. No live router probes were initiated by the review server.

Installed packages inspected: Flask 3.1.3 and Werkzeug 3.1.9. This verifies the tested environment and dependency compatibility, not the absence of published or future CVEs. No dependency-advisory scanner was installed or run.

## Remaining limitations and publication considerations

1. **Local prototype, not a public production service.** Flask's development server is retained solely for local development/demo. There is no authentication, per-user ownership model, distributed scheduling, or multi-process lock/rate limiter. Host/origin checks do not protect against a malicious process already running as the local user. Permission assertions cannot prove network ownership.
2. **OS DNS cancellation:** Python cannot safely cancel the OS resolver thread. The five-second response deadline retains the only slot until the resolver actually completes; repeated calls cannot accumulate stuck workers. A permanently stuck resolver can require an application restart.
3. **OS/localization dependence:** malformed or unsupported localized ping/tracert output is treated conservatively as unavailable/incomplete. Linux/macOS ping flags are implemented, but this audit's real OS checks ran on Windows. Traceroute is Windows-only.
4. **Sampled evidence only:** counts, observations, and packet loss do not establish uninterrupted reachability, exact outage timing, bandwidth, or application-level service health. ICMP may be filtered. An open TCP port establishes a connection, not a healthy application.
5. **History growth and backups:** retention remains unbounded to preserve user data. Use SQLite backup or stop the app before copying a WAL database. No cleanup/migration rewrote existing result rows during this audit. The ID-reuse fix prevents future cross-associations; the current schema cannot reconstruct an intended target if such a race had already affected an older row.
6. **Trusted local configuration:** environment variables, local executable paths, and direct filesystem edits are trusted. API clients cannot change them. A user can deliberately override safe launch configuration; the supplied launch is the reviewed path.
7. **Publication inventory:** `.gitignore` excludes the current database/sidecars, common alternate SQLite filenames, virtual environments, `.env` files, and logs. There is no Git repository in this folder, so a tracked-file/index/history review was not possible. Inspect custom database extensions and any files already tracked when initializing/importing a repository. No publication was attempted.
8. **Audit limits:** no external penetration test, load test, firewall inventory, exhaustive OS/platform matrix, or live vulnerability-advisory scan was performed. Compatibility checks are not a guarantee that every dependency is vulnerability-free.
