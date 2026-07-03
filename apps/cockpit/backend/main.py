"""HFT3 Cockpit — FastAPI aggregation service.

Run:  uvicorn apps.cockpit.backend.main:app --host 0.0.0.0 --port 8080
Read-only by default; control endpoints are local-origin only (see auth.py).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- self-contained import path (no dependency on hft3_bootstrap shim) ------
_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from apps.cockpit.backend import paths  # noqa: E402
from apps.cockpit.backend.aggregate import ZONES  # noqa: E402
from apps.cockpit.backend.auth import require_view  # noqa: E402
from apps.cockpit.backend.control import router as control_router  # noqa: E402
from apps.cockpit.backend.lifecycle_control import router as lifecycle_control_router  # noqa: E402
from apps.cockpit.backend.hub import Hub  # noqa: E402
from apps.cockpit.backend.watch import Watcher  # noqa: E402

hub = Hub()
watcher = Watcher(hub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher.start(asyncio.get_running_loop())
    try:
        yield
    finally:
        watcher.stop()


app = FastAPI(title="HFT3 Cockpit", version="0.1.0", lifespan=lifespan)

# CORS — front-end origin(s). Tighten in production via COCKPIT_ALLOW_ORIGINS.
import os  # noqa: E402

_origins = os.environ.get("COCKPIT_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- Rate limiting (dependency-free, in-process sliding window) --------------
# Bounds /api/* against abuse when exposed via Caddy. /api/chat (the LLM SSE
# stream) gets a tighter budget; /api/health is exempt so monitors aren't capped.
import time as _time  # noqa: E402
from collections import defaultdict, deque  # noqa: E402

from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

_RL_WINDOW_S = 60.0
_RL_MAX = int(os.environ.get("COCKPIT_RATE_LIMIT_PER_MIN", "120"))
_RL_CHAT_MAX = int(os.environ.get("COCKPIT_CHAT_RATE_LIMIT_PER_MIN", "20"))
_RL_MAX_KEYS = 4096  # cap distinct buckets so the limiter can't be grown without bound
# Behind Caddy every request arrives from 127.0.0.1, so without this the limit is
# one global bucket (one client can lock out the rest). Opt in when a trusted proxy
# fronts the cockpit to key on the real client via X-Forwarded-For.
_RL_TRUST_PROXY = os.environ.get("COCKPIT_TRUST_PROXY", "") == "1"
_RL_TRUSTED_PROXIES = {
    h.strip()
    for h in os.environ.get("COCKPIT_TRUSTED_PROXY_HOSTS", "").split(",")
    if h.strip()
}
_rl_hits: dict[str, deque] = defaultdict(deque)


def _client_ip_for_rate_limit(peer_ip: str, x_forwarded_for: str | None) -> str:
    if not (_RL_TRUST_PROXY and peer_ip in _RL_TRUSTED_PROXIES and x_forwarded_for):
        return peer_ip
    chain = [p.strip() for p in x_forwarded_for.split(",") if p.strip()]
    return chain[0] if chain else peer_ip


@app.middleware("http")
async def _rate_limit(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        is_chat = path == "/api/chat"
        ip = request.client.host if request.client else "?"
        ip = _client_ip_for_rate_limit(ip, request.headers.get("x-forwarded-for"))
        key = f"{ip}:{'chat' if is_chat else 'api'}"
        limit = _RL_CHAT_MAX if is_chat else _RL_MAX
        now = _time.monotonic()
        dq = _rl_hits[key]
        while dq and now - dq[0] > _RL_WINDOW_S:
            dq.popleft()
        if len(dq) >= limit:
            return JSONResponse(
                {"error": "rate limit exceeded", "limit_per_min": limit},
                status_code=429,
            )
        dq.append(now)
        # The limiter must not become its own memory-exhaustion vector: when the
        # bucket count blows past the cap, drop idle buckets, then hard-reset under
        # a genuine flood (fail-open on TRACKING, never on the limit itself).
        if len(_rl_hits) > _RL_MAX_KEYS:
            for k in [k for k, v in list(_rl_hits.items()) if not v or now - v[-1] > _RL_WINDOW_S]:
                _rl_hits.pop(k, None)
            if len(_rl_hits) > _RL_MAX_KEYS:
                _rl_hits.clear()
    return await call_next(request)


app.include_router(control_router)
app.include_router(lifecycle_control_router)


def _zone(name: str) -> dict:
    try:
        return ZONES[name]()
    except Exception as exc:
        return {"zone": name, "error": str(exc), "generated_utc": paths.now_iso()}


_DIR_LIST_LIMIT = 200


def _artifact_roots() -> tuple[Path, ...]:
    roots = (
        paths.REPO / "artifacts",
        paths.REPO / "research_cards",
        paths.REPO / "reports",
        paths.REPO / "runtime" / "reports",
        paths.REPO / "runtime" / "latency_reports",
        paths.REPO / "runtime" / "validation",
        paths.REPO / "runtime" / "monitor",
        paths.REPO / "runtime" / "workbench",
    )
    return tuple(root.resolve() for root in roots)


def _safe_artifact_path(requested: str) -> Path:
    raw = (requested or "").strip().replace("\\", "/")
    rel = Path(raw)
    if not raw or rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="repo-relative artifact path required")
    try:
        candidate = (paths.REPO / rel).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="invalid artifact path") from None
    if not any(candidate == root or root in candidate.parents for root in _artifact_roots()):
        raise HTTPException(status_code=403, detail="artifact path outside allowed roots")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="artifact path not found")
    return candidate


def _artifact_listing(directory: Path) -> dict:
    entries = []
    children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for child in children[:_DIR_LIST_LIMIT]:
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": child.relative_to(paths.REPO).as_posix(),
                "type": "dir" if child.is_dir() else "file",
                "size_bytes": stat.st_size if child.is_file() else None,
                "modified_utc": datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
            }
        )
    return {
        "path": directory.relative_to(paths.REPO).as_posix(),
        "type": "dir",
        "entries": entries,
        "truncated": len(children) > _DIR_LIST_LIMIT,
    }


def _artifact_file_response(path: Path) -> FileResponse:
    suffix = path.suffix.lower()
    media_type = "text/plain" if suffix in {".html", ".htm", ".svg"} else None
    response = FileResponse(path, media_type=media_type)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ws_clients": hub.count, "execution_mode": paths.execution_mode()}


@app.get("/api/pipeline")
def pipeline(_: str = Depends(require_view)) -> dict:
    return _zone("pipeline")


@app.get("/api/portfolio")
def portfolio(_: str = Depends(require_view)) -> dict:
    return _zone("portfolio")


@app.get("/api/models")
def models(_: str = Depends(require_view)) -> dict:
    return _zone("models")


@app.get("/api/options")
def options(_: str = Depends(require_view)) -> dict:
    return _zone("options")


@app.get("/api/lifecycle")
def lifecycle(_: str = Depends(require_view)) -> dict:
    return _zone("lifecycle")


@app.get("/api/autonomy")
def autonomy(_: str = Depends(require_view)) -> dict:
    return _zone("autonomy")


@app.get("/api/system")
def system(_: str = Depends(require_view)) -> dict:
    return _zone("system")


@app.get("/api/alerts")
def alerts(_: str = Depends(require_view)) -> dict:
    return _zone("alerts")


@app.get("/api/esq")
def esq(_: str = Depends(require_view)) -> dict:
    return _zone("esq")


@app.get("/api/artifact")
def artifact(path: str = Query(..., min_length=1), _: str = Depends(require_view)):
    target = _safe_artifact_path(path)
    if target.is_dir():
        return _artifact_listing(target)
    if target.is_file():
        return _artifact_file_response(target)
    raise HTTPException(status_code=404, detail="artifact path is not a file or directory")


@app.get("/api/all")
def all_zones(_: str = Depends(require_view)) -> dict:
    return {name: _zone(name) for name in ZONES}


@app.get("/api/model/{hyp_id}")
def model_detail(hyp_id: int, _: str = Depends(require_view)) -> dict:
    from apps.cockpit.backend.aggregate import model_detail as md

    try:
        return md.build(hyp_id)
    except Exception as exc:
        return {"id": hyp_id, "error": str(exc), "generated_utc": paths.now_iso()}


from fastapi import Body  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402


@app.post("/api/chat")
async def chat(payload: dict = Body(...), _: str = Depends(require_view)):
    """Read-only advisory chat with the local Gemma model. SSE token stream."""
    from apps.cockpit.backend import chat as chat_mod

    query = str((payload or {}).get("query", "")).strip()
    if not query:
        return {"error": "query required"}
    if len(query) > 4000:
        return {"error": "query too long (max 4000 chars)"}
    return StreamingResponse(chat_mod.stream_chat(query), media_type="text/event-stream")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # WS auth: token via ?token= query (browsers can't set WS headers easily).
    token = websocket.query_params.get("token")
    vt = os.environ.get("COCKPIT_VIEW_TOKEN")
    ct = os.environ.get("COCKPIT_CONTROL_TOKEN")
    client = websocket.client.host if websocket.client else ""
    if vt or ct:
        if token not in (vt, ct) or token is None:
            await websocket.close(code=4401)
            return
    elif client not in {"127.0.0.1", "::1", "localhost"}:
        await websocket.close(code=4401)
        return

    await hub.connect(websocket)
    # First paint: send every zone immediately.
    for name in ZONES:
        await websocket.send_json({"zone": name, "payload": _zone(name)})
    try:
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("action") == "subscribe":
                zones = set(msg.get("zones") or [])
                await hub.set_zones(websocket, zones & set(ZONES))
            elif isinstance(msg, dict) and msg.get("action") == "refresh":
                for name in ZONES:
                    await websocket.send_json({"zone": name, "payload": _zone(name)})
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)


# Serve the built SPA (single origin) if built. Hashed assets are served from
# /assets; every other non-API path falls back to index.html so client-side
# routes (/models, /chat, ...) work on deep-link + refresh (the catch-all is
# added LAST, so the explicit /api and /ws routes always win).
_DIST = _REPO / "apps" / "cockpit" / "frontend" / "dist"
_INDEX = _DIST / "index.html"
if _DIST.is_dir() and _INDEX.is_file():
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    _DIST_ROOT = _DIST.resolve()

    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # Serve a real static file ONLY if it resolves INSIDE dist. The resolve()
        # + parents containment check rejects `../` traversal (incl. URL-encoded
        # %2e%2e%2f) and absolute/drive-letter inputs, so this unauthenticated
        # route cannot leak backend source or any other process-readable file
        # (e.g. a .env with credentials). Otherwise fall back to the SPA
        # entrypoint so client-side routes work on deep-link/refresh.
        if full_path:
            try:
                candidate = (_DIST_ROOT / full_path).resolve()
            except (OSError, ValueError):
                candidate = None
            if candidate is not None and candidate.is_file() and _DIST_ROOT in candidate.parents:
                return FileResponse(candidate)
        if _INDEX.is_file():
            return FileResponse(_INDEX)
        raise HTTPException(status_code=404)
