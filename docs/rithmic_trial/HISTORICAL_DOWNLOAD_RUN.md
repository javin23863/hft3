# Rithmic Historical Data Download — CHI404 Run Reference

Companion to `scripts/rithmic_download_test.py`. Read this on CHI404 before
running the download proof. The script **refuses to run on Windows**
(BLUEPRINT §4, AGENTS.md topology rule).

## Environment variables

Put these in `/root/hft3/.env` on CHI404. Do **not** commit `.env`.

```
RITHMIC_USER=<broker-issued username>
RITHMIC_PASSWORD=<broker-issued password>
RITHMIC_SYSTEM_NAME="Rithmic Test"     # exact string from broker
RITHMIC_URL="wss://rituz00100.00.rithmic.com:443"
RITHMIC_APP_NAME=HFT3
RITHMIC_APP_VERSION=1.0
```

`RITHMIC_SYSTEM_NAME` must match the server-side string byte-for-byte
(capitalization, spaces). The broker doc header in the most recent email
says `Rithmic Test` (with the space). Use that exact value, in double
quotes, so the space survives shell parsing.

`RITHMIC_URL` defaults to `wss://rituz00100.00.rithmic.com:443`. The Rithmic
gateway currently returns a cert with SAN `*.rithmic.com`, so any of
`rituz00100.00.{rithmic.com, theomne.net, theomne.com}` will match. Use
`rithmic.com` first; if it fails, try the others in turn. The Windows
workstation cannot reach `rithmic.net` (DNS blocked from this network)
but CHI404 can.

## Phase 1 — Probe only (no data download)

The probe connects, lists valid `system_name` values from `get_system_info()`,
and exits. Use this to confirm the URL + credentials work before requesting
historical data.

```bash
cd /root/hft3/repo
python scripts/rithmic_download_test.py --probe-only
```

**Expected output (success):**
```
REFUSED / connection refused on Windows — must run on CHI404
connected: True
...
PROBE FAILED: ...   <- only on failure
valid system_name values: ['Rithmic Test', 'Rithmic Paper Trading', ...]
configured system_name 'Rithmic Test' is VALID
```
Exit code: `0` if the configured `RITHMIC_SYSTEM_NAME` is in the list,
`1` if it's missing from the list, `1` if the connection failed.

**Common failure modes:**

| Error | Fix |
|---|---|
| `ssl.SSLCertVerificationError: ... CN name does not match` | URL hostname doesn't match the cert. Try the next host in the broker list (`rithmic.com` → `rithmic.net` → `theomne.net` → `theomne.com`). |
| `ssl.SSLCertVerificationError: ... self-signed certificate in certificate chain` | `async_rithmic`'s bundled root CA is `USERTrust RSA Certification Authority`, but the current Rithmic chain is signed by `Sectigo Public Server Authentication CA DV R36`. Pass `--ssl-ca-file /path/to/with_sectigo.crt` to inject the right intermediate. The intermediate is available at `http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt`. On CHI404 (Linux, stdlib ssl only, no `pip._vendor.truststore` injection) the system root store usually handles this; if not, build a chain file with the Sectigo intermediate + the USERTrust root and pass it. |
| `ConnectionRefusedError` or `TimeoutError` | Host is unreachable from CHI404 or the WS port is firewalled. Verify with `curl -vI https://<host>:443` (will fail at app layer, but confirms TCP+TLS reachability). |
| `PROBE FAILED: ... system_name not in [...]` | `RITHMIC_SYSTEM_NAME` doesn't match. Read the `valid system_name values: [...]` line, set the env to one of them, retry. |
| `PROBE FAILED: ... historical data entitlement ...` | Account doesn't have History Plant access. Contact broker to enable historical-tick entitlement on the paper account. |

## Phase 2 — 1-minute historical tick download

Only run after Phase 1 prints `configured system_name '...' is VALID` and
exits `0`. Use a fixed-dated contract (not a front-month) for the first
request — front-month symbols roll over and may not be what you expect.

```bash
cd /root/hft3/repo
python scripts/rithmic_download_test.py \
  --symbol ESM5 --exchange CME \
  --start 2025-05-15T15:30:00Z --end 2025-05-15T15:31:00Z
```

**Expected output (success):**
```
connected: True
requested symbol: ESM5
requested exchange: CME
requested start: 2025-05-15T15:30:00+00:00
requested end:   2025-05-15T15:31:00+00:00
data type: ticks
max_pages: 200
number of ticks returned: <N>
first timestamp: 2025-05-15T15:30:00.xxxxxx+00:00
last timestamp:  2025-05-15T15:30:59.xxxxxx+00:00
returned fields: ['timestamp', 'price', 'size', ...]
inferred data label: ticks
parquet: /root/hft3/repo/data/raw/rithmic_test/symbol=ESM5/date=2025-05-15/ticks.parquet
manifest: /root/hft3/repo/data/raw/rithmic_test/symbol=ESM5/date=2025-05-15/manifest.json
```

Exit code: `0` on success, `1` on any error (manifest is still written
with `error: ...` populated).

**Read the manifest before scaling:**
```bash
cat /root/hft3/repo/data/raw/rithmic_test/symbol=ESM5/date=2025-05-15/manifest.json
```

Check:
- `row_count > 0` — if zero, the window is outside Rithmic retention or
  the symbol is invalid for that exchange.
- `data_label` is `ticks`, `bars`, `depth/mbp`, or `mbo`. Per the hard
  labeling rule: do **not** accept `mbo` unless the schema proves
  order-level events (`order_id` + `action` + `side` + `flags` with depth
  context). The script's heuristic is conservative; the manifest is the
  ground truth.
- `first_timestamp` ≥ `request_start` and `last_timestamp` ≤ `request_end`.
- `returned_columns` matches the expected schema for the labeled data type.

## Phase 3 — Broader window (after Phase 2 is green)

```bash
python scripts/rithmic_download_test.py \
  --symbol ESM5 --exchange CME \
  --start 2025-05-15T14:30:00Z --end 2025-05-15T15:30:00Z \
  --max-pages 1000
```

`async_rithmic` handles pagination internally; `--max-pages 1000` is the
library's per-request cap and is sufficient for multi-day windows in
practice. Watch the wall-clock time and tick count in the manifest to
gauge throughput.

## Acceptance criteria for "we can download from Rithmic now"

All must be true:

1. Script runs on CHI404, exits `0`.
2. `manifest.json` exists at the expected path under `data/raw/rithmic_test/`.
3. `row_count > 0`.
4. `first_timestamp` ≥ `request_start`, `last_timestamp` ≤ `request_end`.
5. `data_label` matches the actual columns in `ticks.parquet`.
6. No GUI/screen-scraping path was used.
7. Parquet opens cleanly:
   `python -c "import pyarrow.parquet as pq; print(pq.read_table('<path>').schema)"`.
8. Repeat the same run → same row count and same first/last timestamps
   (deterministic, not a fluke).

## Risks / constraints

- **40 GB/wk per-user cap** for historical data. Track weekly volume if
  you scale past multi-day downloads.
- **MBO mislabel risk**: the script's `_infer_data_label` is conservative;
  eyeball the first manifest before trusting the label.
- **Front-month drift**: prefer fixed-dated symbols (`ESM5`, `ESM6`, ...)
  over `ES` for the first calls; the broker may resolve the bare root
  differently than expected.
- **Credentials on the workstation**: never. The script refuses to run
  there. The Windows-Python `pip._vendor.truststore` package also
  injects into `ssl` and breaks hostname verification for some wildcard
  certs, so the workstation can't even reach the gateway cleanly. CHI404
  (Linux, stdlib `ssl`) doesn't have either problem.
- **Account exposure**: paper account creds were shared in chat. Treat
  them as exposed; rotate before any production use.
