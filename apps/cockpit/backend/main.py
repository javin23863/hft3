"""HFT3 Cockpit — FastAPI aggregation service.

Run:  uvicorn apps.cockpit.backend.main:app --host 0.0.0.0 --port 8080
Read-only by default; control endpoints are local-origin only (see auth.py).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# --- self-contained import path (no dependency on hft3_bootstrap shim) ------
_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from apps.cockpit.backend import paths  # noqa: E402
from apps.cockpit.backend.aggregate import ZONES  # noqa: E402
from apps.cockpit.backend.auth import require_view  # noqa: E402
from apps.cockpit.backend.control import router as control_router  # noqa: E402
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

app.include_router(control_router)


def _zone(name: str) -> dict:
    try:
        return ZONES[name]()
    except Exception as exc:
        return {"zone": name, "error": str(exc), "generated_utc": paths.now_iso()}


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


@app.get("/api/all")
def all_zones(_: str = Depends(require_view)) -> dict:
    return {name: _zone(name) for name in ZONES}


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


# Serve the built SPA (single origin) if it has been built. Mounted LAST so it
# never shadows the /api or /ws routes above.
_DIST = _REPO / "apps" / "cockpit" / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="spa")
