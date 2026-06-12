# HFT3 Cockpit

Single-pane, real-time trader dashboard for the CME microstructure system.
**Pure aggregation layer** — it reads the artifacts the pipeline already writes,
normalizes them into four zones, and pushes deltas over WebSocket. It never
writes to the trading core, never places an order, never flips `EXECUTION_MODE`.
Detect-only doctrine is binding.

```
apps/cockpit/
  backend/   FastAPI : REST per zone + WS push + local-origin control plane
  frontend/  (W3) Next/React : 4-zone responsive cockpit
```

## Zones

| Zone | Endpoint | What it shows |
|---|---|---|
| Pipeline | `GET /api/pipeline` | Capture → Feature Build → Stage A → Gauntlet B → M6 Gate → Promote, each with live status + counts |
| Portfolio | `GET /api/portfolio` | PnL / positions / fills. `live_session:false` until `EXECUTION_MODE=LIVE` (shows latest replay fills meanwhile) |
| Models | `GET /api/models` | 50-hypothesis grid + promotion funnel + **silent-zero** badge (structurally-dead hyps surfaced, not hidden) |
| System | `GET /api/system` | Latency truth card, slow-tier LLM, certification GREEN/RED, Databento budget, capture, execution mode |
| Alerts | `GET /api/alerts` | Problem-only roll-up — quiet when healthy |
| (all) | `GET /api/all` | Every zone in one response (first paint) |
| Live | `WS /ws?token=…` | First-paint all zones, then `{zone,payload}` deltas on any artifact change |

`GET /api/health` is open (no auth); everything else requires the `view` scope.

## Auth

Two scopes, set via env:

- `COCKPIT_VIEW_TOKEN` — bearer token for read endpoints + WS (`?token=`).
- `COCKPIT_CONTROL_TOKEN` — bearer for `/api/control/*`. **Control is local-origin
  only**: a proxied (remote) request carries `X-Forwarded-For` and is refused
  regardless of token. Order placement / mode flips are never web-exposed.

If no token is set, read access is allowed from loopback only (dev) and refused
remotely — never silently world-open. Job execution is additionally gated by
`COCKPIT_CONTROL_EXEC=1` (off by default; W4 wires the tracked launcher).

## Run

**Backend only (dev, with separate Vite dev server):**

```powershell
# from repo root
$env:PYTHONPATH="C:/Users/MSI/.claude/shims;C:/Users/MSI/repos/hft3;C:/Users/MSI/repos/hft3/packages"
$env:COCKPIT_VIEW_TOKEN="<pick-one>"
python -m uvicorn apps.cockpit.backend.main:app --host 0.0.0.0 --port 8080
# in another shell:  cd apps/cockpit/frontend && npm run dev   (proxies /api + /ws)
```

Or: `scripts/run_cockpit.ps1`.

**Single-origin (build the SPA once; backend serves it at `/`):**

```powershell
cd apps/cockpit/frontend; npm install; npm run build   # -> dist/
# then run the backend as above; http://host:8080/ now serves the cockpit.
```

When `apps/cockpit/frontend/dist/` exists the backend auto-mounts it at `/`
(after the API routes, so `/api` and `/ws` are never shadowed). One process,
one origin — ideal behind Caddy.

## Automations

- **Problem-only push** (`push.py`): a notification fires ONLY when a *new*
  warn/crit alert appears — never on healthy state, never repeatedly for a
  standing problem; a cleared-then-recurring problem re-alerts. Configure one
  channel: `NTFY_URL` (e.g. `https://ntfy.sh/your-topic`) or
  `COCKPIT_NOTIFY_WEBHOOK`. Unset = silent (diff still tracked).
- **File-watcher push**: any artifact change recomputes the affected zones and
  pushes deltas over WS (no polling).

## Control plane

`/api/control/*` (local-origin only). `GET /status` lists jobs + recent audit.
`POST /databento/preflight` returns a live cost estimate (read-only, never
downloads). `POST /job {name, confirm:true}` is **audit-logged but execution is
gated off** unless `COCKPIT_CONTROL_EXEC=1` — the live tracked-subprocess
launcher is intentionally not wired so the dashboard can never kick off a
rebuild/service-restart by accident. No endpoint places an order or flips
`EXECUTION_MODE`.

### Remote / mobile (TLS)

Put Caddy in front for TLS so the phone can reach it. Caddy runs on the same
host and reverse-proxies to `127.0.0.1:8080`; because proxied requests carry
`X-Forwarded-For`, they automatically get `view` scope only — the control plane
stays bound to direct-local calls.

```
# Caddyfile
cockpit.your-domain.tld {
    reverse_proxy 127.0.0.1:8080
}
```

## Tests

```powershell
python -m pytest apps/cockpit/backend/tests -q
```

## Boundaries

Read-only by default. No order placement, no manipulation controls. Credential
*values* are never rendered (presence only). The dashboard surfaces silent-zero
feature gaps rather than hiding them (honest-ledger principle).
