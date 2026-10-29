# Configuration

Environment variables are read at application startup. Set them in PowerShell before starting `app.py`; restart to apply changes. NetScope does not load `.env` files automatically. Do not commit machine-specific configuration, databases or operator notes.

## Windows setup and compatibility

The verified runtime baseline is Windows x64 with CPython **3.10.7**, Windows `ping.exe` and `tracert.exe`, and a modern JavaScript/Canvas/SVG browser. Node.js **24.14.1** is needed only for the documented frontend development checks; no npm install is required to run the app. Other interpreter/OS versions and localized probe output require separate verification; dormant non-Windows ping branches do not establish Linux/macOS support.

From the project root (the folder containing `app.py`), install and run using the [README quick start](../README.md#quick-start). Activation and execution-policy changes are unnecessary. Use the direct `app.py` launcher, one process, no reloader/debug mode; do not substitute `flask run`, multiple workers, a tunnel, proxy or public bind. Use the same localhost/127.0.0.1 origin for all dashboard interactions. Stop normally with Ctrl+C.

Startup configures rotating logs, initializes or migrates a compatible database, and starts automatic monitoring. A fresh database seeds Local computer at `127.0.0.1:80`; a refused port is a valid negative TCP result independent of ICMP. Existing devices/history and intentionally empty inventories are preserved. Unknown/incompatible schemas fail rather than reset data. Back up private data before upgrades; no downgrade/repair tool is supplied.

Use the exact runtime and development lock files for the verified package snapshot. The ranges in `requirements.txt` and `requirements-dev.txt` are maintenance inputs. Locked versions are not artifact hashes, a vulnerability certification or cross-platform compatibility proof. The prior clean-install check reconstructed wheels offline from installed packages; it did not verify fresh PyPI artifact provenance. See the [release checklist](RELEASE_CHECKLIST.md) for the isolated verifier and full test procedure.

## Monitoring and storage

| Variable | Default | Accepted value / behavior |
| --- | --- | --- |
| `NETSCOPE_DB` | `netscope.db` beside `app.py` | SQLite file path. A relative override is relative to the process working directory. Startup creates its parent directory; keep database and sidecars together/private. |
| `NETSCOPE_CHECK_INTERVAL` | `30` | 1–3600 seconds **after** a batch finishes, not a fixed start-to-start frequency. |
| `NETSCOPE_MONITOR_WORKERS` | `4` | Integer 1–16; bounded device workers. |
| `NETSCOPE_ICMP_TIMEOUT_MS` | `1000` | 100–10000 ms per echo wait, converted to integer milliseconds. Five echoes per monitoring cycle, plus an overall command deadline. |
| `NETSCOPE_TCP_TIMEOUT` | `1.5` | 0.1–30 seconds per TCP connection. |
| `NETSCOPE_DISCOVERY_TIMEOUT_MS` | `500` | 100–1000 ms, converted to integer milliseconds; separate discovery echo wait. |

Invalid numeric values stop import/startup instead of silently using unsafe values. The localhost seed uses TCP port 80; refusal is a valid result and does not imply an ICMP failure. Existing or intentionally empty databases do not receive another seed.

## Incidents

| Variable | Fresh-database default | Accepted values |
| --- | --- | --- |
| `NETSCOPE_INCIDENT_OPEN_CHECKS` | `3` | Integer 1–100; consecutive qualifying bad observations. |
| `NETSCOPE_INCIDENT_RECOVERY_CHECKS` | `2` | Integer 1–100; consecutive valid recovery observations. |
| `NETSCOPE_INCIDENT_MAX_GAP_SECONDS` | `300` | Integer 30–86400; larger observation gaps reset pending streaks. |
| `NETSCOPE_INCIDENT_LOSS_PCT` | `40` | Finite 0–100; bad when valid loss is **strictly above** the threshold. |
| `NETSCOPE_INCIDENT_LATENCY_MS` | `200` | Finite 0–60000; bad when eligible exact RTT is strictly above the threshold. |

ICMP and TCP unavailable thresholds are fixed at a failed classifiable check; they remain independent. Opening/recovery/gap overrides apply to all conditions. Explicit environment values update persisted `alert_config` during initialization; absent variables retain the database's current settings. Changes increment revisions and reset pending streaks at the next new observation, preserving incident history. Removing an override does not restore factory defaults. Unknown outcomes never open or resolve incidents.

## Logs

| Variable | Default | Accepted values |
| --- | --- | --- |
| `NETSCOPE_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL. |
| `NETSCOPE_LOG_DIR` | `logs` beside `app.py` | Private writable directory; relative overrides use the working directory. |
| `NETSCOPE_LOG_MAX_BYTES` | `1048576` | Integer 1024–104857600; rotation threshold. |
| `NETSCOPE_LOG_BACKUPS` | `3` | Integer 1–20; rotated backups in addition to the active file. |

The launcher configures console and rotating `netscope.log` JSON lines. Importing modules does not configure files. Records keep event/severity/time and allowlisted identifiers/counts, not raw bodies, device addresses/names, notes, exception messages or tracebacks. Class names remain available for execution failures. Expected device timeouts/refusals are measurements rather than per-probe application errors. Bad log configuration or an unwritable directory stops startup. Custom directories outside the repository still require private handling.

## Example

```powershell
$env:NETSCOPE_DB = Join-Path $PWD 'private-data\netscope.db'
$env:NETSCOPE_MONITOR_WORKERS = '4'
$env:NETSCOPE_CHECK_INTERVAL = '30'
$env:NETSCOPE_ICMP_TIMEOUT_MS = '1000'
$env:NETSCOPE_TCP_TIMEOUT = '1.5'
$env:NETSCOPE_DISCOVERY_TIMEOUT_MS = '500'
$env:NETSCOPE_INCIDENT_OPEN_CHECKS = '3'
$env:NETSCOPE_INCIDENT_RECOVERY_CHECKS = '2'
$env:NETSCOPE_INCIDENT_LOSS_PCT = '40'
$env:NETSCOPE_INCIDENT_LATENCY_MS = '200'
$env:NETSCOPE_LOG_LEVEL = 'INFO'
$env:NETSCOPE_LOG_DIR = Join-Path $PWD 'logs'
.\.venv\Scripts\python.exe app.py
```

This starts normal automatic monitoring of the stored target list. Changing `NETSCOPE_DB` selects a different file; it does not copy or migrate another file into it. Back up existing data privately before upgrading. Do not start multiple NetScope processes against the same database.

## Fixed boundaries

There are no configurable remote bind/authentication settings. The launcher binds `127.0.0.1:5000`, disables debug/reloader, and requires localhost/127.0.0.1 Host plus loopback peer and same-origin requests. Only RFC1918 and loopback IPv4 literals can become monitoring/discovery/traceroute destinations. DNS resolves manually requested public domain names but never enrolls/connects to results.

Discovery has two separate workers, one active job, two accepted scans/minute and at most 32 usable hosts per explicit authorized request. Dashboard polling is ten seconds; manual/discovery job polling is one second while active. History charts cap at 2,000 points/device. Detailed query, retention and report limits are in the [feature/API reference](REFERENCE.md). These constants are not environment variables.
