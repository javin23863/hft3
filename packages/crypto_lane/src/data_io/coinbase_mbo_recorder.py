"""Coinbase Exchange WebSocket ``full`` channel recorder — true order-level MBO.

Feed: wss://ws-feed.exchange.coinbase.com
The ``full`` channel requires Exchange API authentication (level2/full/level3 are gated).
See: https://docs.cdp.coinbase.com/exchange/docs/websocket-auth
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
DEFAULT_PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD"]
RECONNECT_DELAY_S = 5.0

MBO_MESSAGE_TYPES = frozenset({"received", "open", "change", "done", "match"})

_ENV_KEY = ("COINBASE_EXCHANGE_API_KEY", "COINBASE_API_KEY", "HFT3_CRYPTO_COINBASE_API_KEY")
_ENV_SECRET = ("COINBASE_EXCHANGE_API_SECRET", "COINBASE_API_SECRET", "HFT3_CRYPTO_COINBASE_API_SECRET")
_ENV_PASSPHRASE = (
    "COINBASE_EXCHANGE_API_PASSPHRASE",
    "COINBASE_API_PASSPHRASE",
    "HFT3_CRYPTO_COINBASE_API_PASSPHRASE",
)


def _env_first(*names: str) -> str:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def _exchange_ws_auth() -> Dict[str, str]:
    from crypto_lane.src.config.env_loader import ensure_crypto_env

    ensure_crypto_env()
    key = _env_first(*_ENV_KEY)
    secret = _env_first(*_ENV_SECRET)
    passphrase = _env_first(*_ENV_PASSPHRASE)
    missing = [
        n
        for n, val in zip(_ENV_KEY[:1] + _ENV_SECRET[:1] + _ENV_PASSPHRASE[:1], (key, secret, passphrase))
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Coinbase Exchange ``full`` channel requires API credentials. "
            f"Set one of each: {_ENV_KEY[0]}, {_ENV_SECRET[0]}, {_ENV_PASSPHRASE[0]} "
            "(or the HFT3_CRYPTO_COINBASE_* aliases in .env / CRYPTO_KEYS_ENV)."
        )
    timestamp = str(int(time.time()))
    message = timestamp + "GET" + "/users/self/verify"
    hmac_key = base64.b64decode(secret)
    signature = base64.b64encode(
        hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "key": key,
        "passphrase": passphrase,
        "timestamp": timestamp,
        "signature": signature,
    }


def _resolve_output_dir() -> Path:
    from crypto_lane.src.ingest.paths import data_root

    return data_root() / "coinbase_mbo_raw"


def _session_filename(product_id: str, output_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = product_id.replace("-", "_")
    return output_dir / f"coinbase_mbo_{safe}_{ts}.ndjson"


class CoinbaseMboRecorder:
    """Records Coinbase Exchange ``full`` channel messages to NDJSON."""

    def __init__(
        self,
        product_ids: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
    ):
        self.product_ids = product_ids or list(DEFAULT_PRODUCTS)
        self.output_dir = output_dir or _resolve_output_dir()
        self._running = True
        self._stop_requested = False
        self._files: Dict[str, Any] = {}
        self._msg_counts: Dict[str, int] = {}

    def _write_line(self, product_id: str, data: Dict[str, Any]) -> None:
        line = json.dumps(data, ensure_ascii=False, default=str)
        self._files[product_id].write(line + "\n")
        self._files[product_id].flush()
        self._msg_counts[product_id] = self._msg_counts.get(product_id, 0) + 1

    async def _subscribe(self, ws: Any) -> None:
        sub: Dict[str, Any] = {
            "type": "subscribe",
            "product_ids": self.product_ids,
            "channels": ["full"],
        }
        sub.update(_exchange_ws_auth())
        await ws.send(json.dumps(sub))

    async def handle_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return

        if not isinstance(data, dict):
            return

        msg_type = data.get("type", "")
        if msg_type in ("subscriptions", "heartbeat"):
            return
        if msg_type == "error":
            reason = data.get("reason") or data.get("message") or data
            logger.error("Coinbase WS error: %s", reason)
            self._running = False
            return
        if msg_type not in MBO_MESSAGE_TYPES:
            return

        product_id = str(data.get("product_id") or "")
        if product_id not in self._files:
            return

        data["_local_ts_utc"] = datetime.now(timezone.utc).isoformat()
        self._write_line(product_id, data)

    async def run(self, duration_s: Optional[float] = None) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._files = {
            pid: open(_session_filename(pid, self.output_dir), "w", encoding="utf-8")
            for pid in self.product_ids
        }
        start = time.monotonic()

        try:
            while self._running and not self._stop_requested:
                try:
                    async with websockets.connect(COINBASE_WS_URL, ping_interval=20, ping_timeout=20) as ws:
                        await self._subscribe(ws)
                        while self._running and not self._stop_requested:
                            if duration_s and (time.monotonic() - start) > duration_s:
                                self._running = False
                                break
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                continue
                            await self.handle_message(raw)
                except websockets.ConnectionClosed:
                    if duration_s and (time.monotonic() - start) > duration_s:
                        break
                    await asyncio.sleep(RECONNECT_DELAY_S)
                except OSError as exc:
                    logger.warning("Coinbase WS connection error: %s", exc)
                    await asyncio.sleep(RECONNECT_DELAY_S)
        finally:
            for fh in self._files.values():
                try:
                    fh.close()
                except Exception:
                    pass
            self._files = {}

        return {
            "products": self.product_ids,
            "output_dir": str(self.output_dir),
            "duration_s": time.monotonic() - start,
            "messages_per_product": dict(self._msg_counts),
        }


def cmd_record_coinbase_mbo(args: Any) -> int:
    products = args.products.split(",") if args.products else DEFAULT_PRODUCTS
    output_dir = Path(args.output_dir) if args.output_dir else None

    recorder = CoinbaseMboRecorder(product_ids=products, output_dir=output_dir)
    summary = asyncio.run(recorder.run(duration_s=args.duration))
    print(json.dumps(summary))
    return 0
