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

## Manual acceptance before publishing

- Choose a repository license. **MIT is a reasonable recommendation** for a permissive portfolio project, but it is not selected or added without the owner's authorization. Until a license is granted, do not describe the project as MIT-licensed or generally licensed for reuse. Review third-party notices separately.
- Run the documented launcher with an isolated disposable database and only systems you own/are authorized to test. Keep personal runtime files private. Verify startup, Ctrl+C shutdown, automatic/manual monitoring, busy responses and persistence/restart in an actual browser. Automated launcher checks replace network/HTTP execution, so they do not prove OS/browser integration.
- Verify Windows `ping.exe` parsing on the actual OS locale and `tracert.exe` behavior on an explicitly authorized destination. No real discovery scan is part of automated release verification. Authorize any small manual discovery scan separately, including its exact CIDR.
- Review incident opening/acknowledgement/recovery, in-app unread/read state, and optional browser notification permission/denial. OS desktop delivery, closed-page limitations, multi-tab races and mobile support need manual review.
- Exercise map pan/zoom/filtering/keyboard navigation, empty/stale states, responsive layout and screen-reader labels. Confirm that address buckets remain visibly labeled display groupings with no topology edges.
- Export CSV/PDF, inspect actual download dialogs, sensitive fields, formula-safe names, Unicode fallback, empty reports and multi-page readability. Local exports are private data and must not be published as sample artifacts without review.
- Capture current screenshots from a disposable, authorized setup using the [capture checklist](../screenshots/README.md). The two tracked October 15 images are authentic legacy views, not evidence of new feature UI. Add authentic captures of incident/notification controls, discovery consent/progress, the unconnected map, analytics and export controls. Do not substitute mockups or unreviewed private data.
- Review tracked files and `git diff --cached` before any later publication. `.gitignore` does not protect already-tracked files or detect secrets. Current addresses in source/tests are allowlists, loopback examples and controlled fixtures, not a real inventory. `AUDIT.md` is a historical audit, not a current security certification.
- Verify the Mermaid diagram in the intended GitHub renderer; record what parser/render checks actually ran. No diagram edge asserts a real device link.
- Confirm lock installs on the intended clean machine and decide which additional patched Python/Node/Windows versions to test. Do not infer Linux/macOS support from dormant code branches or temporary simulated tests.

## Explicit limitations and optional future work

Local-only deployment is the only documented deployment. No authentication, authenticated audit trail, TLS setup, secure proxy model, multi-process monitor coordination or remote threat model is implemented. Flask's local development server is not a remote production deployment plan. Do not expose it through a tunnel, public interface or proxy.

Newer Python/Node baselines, hash-verified release artifacts, automated CI, dependency vulnerability review and packaging/distribution can be future work. Containers/cloud infrastructure are not required to reproduce this local application and are not included in this task. No license decision, genuine new screenshot capture or manual OS/browser acceptance can be replaced by test counts.
