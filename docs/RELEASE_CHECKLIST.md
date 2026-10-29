# Local release review

Release preparation is documentation/reproducibility work, not publication, remote hosting approval or enterprise certification. No license, GitHub repository, CI service, Docker deployment or remote configuration is added automatically.

## Recorded verification - 23 October 2026

| Check | Actual result |
| --- | --- |
| Verified platform | Windows x64, CPython 3.10.7; Node.js 24.14.1. |
| Fresh runtime installation | Ten exact runtime distributions installed offline into a new virtual environment; no system-site-packages or pypdf available. |
| Fresh development installation | Runtime plus pypdf 6.21.2 / typing_extensions 4.16.0 installed from the development lock; `pip check` passed. |
| Real launcher with simulated adapters | Fresh schema 5 initialization and v0 upgrade passed; preserved legacy target/result, automatic simulated persistence, local bind/debug/reloader arguments and graceful stop/join/drain verified. No HTTP listener or network probes were started by the smoke check. |
| Full Python suite in that clean environment | **260 passed**, **72.232 seconds**, zero failures/errors. Original self-owned loopback regression checks still ran. |
| Full frontend suite | **91 passed**, zero failed/cancelled/skipped, **1769.2595 ms**. |
| Syntax | 38 Python files parsed; all six frontend JavaScript modules passed `node --check`. |
| Documentation | 22 local Markdown links resolve; documented configuration variable names exist in source. |
| Diagrams | Both Mermaid blocks parsed as `flowchart-v2` by temporary Mermaid **11.17.2** with a DOM shim. No renderer image or GitHub browser rendering was verified. |
| Repository checks | `git diff --check` and configured-environment `pip check` passed; ignored-file representative checks passed; no tracked ignored files or new secrets/hardcoded personal paths found. |
| User data | Real database checksum unchanged from the pre-release-preparation local checkpoint; all setup/migration tests used temporary files. |

The verifier bounds install subprocesses to 90 seconds, launcher simulation to 35 seconds and Python discovery to 120 seconds. The frontend command was bounded to 30 seconds. None timed out. The temporary diagram packages were verification-only and are not application dependencies. Broader OS/Python support, a fresh Internet lock installation, upstream artifact/hash verification, vulnerability review, genuine current screenshots and manual browser/Windows integration remain unverified/manual work.

## Automated checks

From the project root, install the pinned development dependencies and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test --test-isolation=none tests/test_dashboard.cjs
Get-ChildItem static\*.js | ForEach-Object { node --check $_.FullName }
node --check tests/test_dashboard.cjs
.\.venv\Scripts\python.exe -m pip check
git diff --check
.\.venv\Scripts\python.exe -B tools/verify_install.py
```

The last command creates a fresh temporary virtual environment, installs exact runtime and test locks from a private wheelhouse rebuilt from installed distributions, checks dependency integrity, exercises the real launcher with mocked probes/HTTP serving, validates fresh and legacy temporary databases, and runs the full Python suite. It never starts discovery or uses the real database. Offline rebuilt wheels verify installation/environment isolation against the configured packages, not upstream artifact provenance, hashes or a fresh network/PyPI download. Binary compatibility verification covers the documented Windows x64 / Python 3.10.7 baseline only. Temporary packages/wheels are not committed.

`requirements.txt` / `requirements-dev.txt` express maintained direct dependency ranges. The `.lock.txt` files pin the verified direct/transitive snapshot for documented installs. Regenerate pins deliberately after dependency changes, then rerun clean verification; do not blindly freeze unrelated environment tools. The locks do not carry artifact hashes or establish that dependencies are free of vulnerabilities. Internet installation of these exact pins and another Python/Node release require separate verification before broadening support claims.

## Remaining gates before a fully accepted release

A clearly labeled source-only portfolio preview may be published with these limitations disclosed. This checklist does not certify production readiness.

- License approved by the owner: [MIT](../LICENSE), Copyright (c) 2026 Sanad Nawayseh. Review third-party notices separately.
- Run the documented launcher with an isolated disposable database and only systems you own/are authorized to test. Keep personal runtime files private. Verify startup, Ctrl+C shutdown, automatic/manual monitoring, busy responses and persistence/restart in an actual browser. Automated launcher checks replace network/HTTP execution, so they do not prove OS/browser integration.
- Verify Windows `ping.exe` parsing on the actual OS locale and `tracert.exe` behavior on an explicitly authorized destination. No real discovery scan is part of automated release verification. Authorize any small manual discovery scan separately, including its exact CIDR.
- Review incident opening/acknowledgement/recovery, in-app unread/read state, incident links, repeated-poll deduplication and reload/restart persistence. Updates require an open dashboard.
- Exercise map pan/zoom/filtering/keyboard navigation, empty/stale states, responsive layout and screen-reader labels. Confirm that address buckets remain visibly labeled display groupings with no topology edges.
- Export CSV/PDF, inspect actual download dialogs, sensitive fields, formula-safe names, Unicode fallback, empty reports and multi-page readability. Local exports are private data and must not be published as sample artifacts without review.
- Capture current screenshots from a disposable, authorized setup using the [capture checklist](../screenshots/README.md). The October 15 legacy images are excluded from the current tree and README; they remain in history. Add authentic captures of incident/notification controls, discovery consent/progress, the unconnected map, analytics and export controls. Do not substitute mockups or unreviewed private data.
- Review tracked files and `git diff --cached` before any later publication. `.gitignore` does not protect already-tracked files or detect secrets. Current addresses in source/tests are allowlists, loopback examples and controlled fixtures, not a real inventory. `AUDIT.md` is a historical audit, not a current security certification.
- Verify the Mermaid diagram in the intended GitHub renderer; record what parser/render checks actually ran. No diagram edge asserts a real device link.
- Confirm lock installs on the intended clean machine and decide which additional patched Python/Node/Windows versions to test. Do not infer Linux/macOS support from dormant code branches or temporary simulated tests.

## Explicit limitations and optional future work

Local-only deployment is the only documented deployment. No authentication, authenticated audit trail, TLS setup, secure proxy model, multi-process monitor coordination or remote threat model is implemented. Flask's local development server is not a remote production deployment plan. Do not expose it through a tunnel, public interface or proxy.

Newer Python/Node baselines, hash-verified release artifacts, automated CI, dependency vulnerability review and packaging/distribution can be future work. Containers/cloud infrastructure are not required to reproduce this local application and are not included in this task. No license decision, genuine new screenshot capture or manual OS/browser acceptance can be replaced by test counts.

## Portfolio preview publication audit — 29 October 2026

- Full Python suite: **261 passed in 65.687 s**, with disposable default database/log paths and isolated fixtures. The real database checksum remained unchanged.
- Full frontend suite: **91 passed in 1270.7163 ms**. All six frontend modules passed `node --check`; `pip check` passed.
- Reviewed all tracked filenames and non-ignored untracked files (none). Runtime DB/sidecars, logs, temporary output, environments, credentials and private CSV/PDF exports are ignored; no such artifacts were tracked in the six reachable commits reviewed.
- Scanned **114 historical blobs across 73 paths** reachable from all refs, plus current tracked text, for private-key headers, common provider-token formats, credential assignments and personal filesystem paths. No matching secret was found. Private IPv4 literals reviewed in source/history are validation ranges, placeholders or deterministic fixtures. Pattern matching cannot prove absence of every possible secret.
- Reviewed both historical screenshot images visually: loopback-only records and generic form placeholders; no personal inventory visible. No EXIF/XMP metadata or user-path marker was detected. They are outdated and excluded from the current tree and README; harmless historical copies remain accessible in earlier commits. Their genuine-capture provenance comes from the prior capture record, not a recreated session.
- Existing commit author name/email remain visible in Git history. No history rewriting, remote configuration or push is performed.
- Reviewed documented Windows virtual-environment, locked install and direct `app.py` launch commands against current dependencies and source. The launcher binds loopback port 5000 with debug/reloader disabled. A fresh upstream/PyPI install, dependency vulnerability certification and current screenshot capture were not performed in this audit.
- **GO for a clearly labeled source portfolio preview**: no critical publication blocker identified within this review. **NO-GO for claiming production readiness or complete manual acceptance.** Historical analytics, real discovery/map, actual report downloads/PDF layout and full responsive/accessibility acceptance remain pending. At the time of this audit no license had been chosen; the owner subsequently approved MIT (see LICENSE).

## Latest pre-screenshot regression checkpoint — 29 October 2026

At local checkpoint `5b3db02ff8d3493b3729985951cfeb7e211c4f8d`, the full Python suite passed **261 tests in 63.471 s** and the frontend suite passed **91 tests in 1090.7111 ms**. Default storage/log paths and fixtures were disposable; the normal database checksum remained unchanged. This supersedes earlier counts for the latest regression summary, not the scope of the earlier offline installation checks. Screenshot/documentation-only changes followed; no new full regression run is claimed for the README redesign.

### Optional simulated benchmarks

These create disposable simulated fixtures and do not run network discovery or use the normal monitoring database:

```powershell
.\.venv\Scripts\python.exe -B tests/benchmark_monitoring.py --repeats 5
.\.venv\Scripts\python.exe -B tests/benchmark_analytics.py
.\.venv\Scripts\python.exe -B tests/benchmark_reports.py
```

For normal development checks, use the Automated checks section above. [AUDIT.md](../AUDIT.md) is a historical source/behavior review, not current release certification.
