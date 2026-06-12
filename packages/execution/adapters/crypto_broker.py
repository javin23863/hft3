"""Bitfinex crypto execution adapters.

Contract source: CRYPTO_LIVE.md §3.
Single-gate K1: _send_order_new is the one and only place transport.order_new is called — grep-enforced.
Mode guards live in safety.py.
C7 wires risk/kill-switch; C8 adds WS order channel + Dead-Man Switch (DMS).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class LatencySample:
    """Paired submit/ack monotonic timestamps for one venue order-new call.

    measurement_tier "rest_sync" = ack boundary is the synchronous REST response
    return (REST adapter has no async order-confirm channel; a WS order-confirm
    tier lands when the authenticated WS order channel is added at live deployment).
    shadow_synthetic must stay False for any sample that feeds an authoritative
    summary (CORRECTNESS.md §2 row 10 discipline mirrored for crypto).
    """

    intent_id: str
    cid: int
    submit_ns: int
    ack_ns: int
    accepted: bool
    measurement_tier: str = "rest_sync"
    shadow_synthetic: bool = False

    @property
    def latency_ms(self) -> float:
        return (self.ack_ns - self.submit_ns) / 1e6


class CryptoRateLimitError(RuntimeError):
    pass


class CryptoTransportError(RuntimeError):
    pass


class BitfinexTransport:
    """Minimal authenticated Bitfinex REST v2 client.

    Keys default from env HFT3_CRYPTO_BITFINEX_API_KEY / HFT3_CRYPTO_BITFINEX_API_SECRET,
    read lazily at first request — constructible keyless for tests.
    """

    # Bitfinex authenticated rate limit ~90 req/min; we budget 80 for headroom.
    _BUCKET_CAPACITY = 80
    _BUCKET_REFILL_INTERVAL = 60.0  # seconds per full bucket

    def __init__(
        self,
        *,
        base_url: str = "https://api.bitfinex.com",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key: Optional[str] = api_key
        self._api_secret: Optional[str] = api_secret
        self._lock = threading.Lock()
        # Token bucket state
        self._bucket_tokens: float = float(self._BUCKET_CAPACITY)
        self._bucket_last_refill: float = time.monotonic()
        # Rate-limit latch (fail-closed per CRYPTO_LIVE.md §3.4)
        self._rate_limited_until: float = 0.0
        # Monotonic nonce guard
        self._last_nonce: int = 0

    def _resolve_keys(self) -> tuple[str, str]:
        key = self._api_key or os.environ.get("HFT3_CRYPTO_BITFINEX_API_KEY", "")
        secret = self._api_secret or os.environ.get("HFT3_CRYPTO_BITFINEX_API_SECRET", "")
        return key, secret

    def _consume_token(self) -> None:
        """Token bucket budgeter, fail-closed: raises CryptoRateLimitError when empty."""
        with self._lock:
            now = time.monotonic()
            # Fail-closed latch check
            if now < self._rate_limited_until:
                raise CryptoRateLimitError("rate-limit latch active until cooldown expires")
            # Refill tokens proportional to elapsed time
            elapsed = now - self._bucket_last_refill
            refill = elapsed * (self._BUCKET_CAPACITY / self._BUCKET_REFILL_INTERVAL)
            self._bucket_tokens = min(float(self._BUCKET_CAPACITY), self._bucket_tokens + refill)
            self._bucket_last_refill = now
            if self._bucket_tokens < 1.0:
                raise CryptoRateLimitError("token bucket exhausted; no queuing per CRYPTO_LIVE.md §3.4")
            self._bucket_tokens -= 1.0

    def _nonce(self) -> str:
        with self._lock:
            candidate = int(time.time() * 1_000_000)
            if candidate <= self._last_nonce:
                candidate = self._last_nonce + 1
            self._last_nonce = candidate
        return str(candidate)

    def _signed_post(self, path: str, body: dict) -> Any:
        self._consume_token()
        key, secret = self._resolve_keys()
        nonce = self._nonce()
        raw_json_body = json.dumps(body, separators=(",", ":"))
        sig_payload = f"/api{path}{nonce}{raw_json_body}".encode()
        signature = hmac.new(secret.encode(), sig_payload, hashlib.sha384).hexdigest()
        headers = {
            "bfx-nonce": nonce,
            "bfx-apikey": key,
            "bfx-signature": signature,
            "content-type": "application/json",
        }
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(
            url,
            data=raw_json_body.encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                with self._lock:
                    self._rate_limited_until = time.monotonic() + 60.0
                raise CryptoRateLimitError(f"HTTP 429 from venue; fail-closed latch armed for 60 s") from exc
            raise CryptoTransportError(f"HTTP {exc.code} from {path}: {exc.reason}") from exc
        except Exception as exc:
            raise CryptoTransportError(f"transport error on {path}: {exc}") from exc

    def order_new(self, body: dict) -> Any:
        return self._signed_post("/v2/auth/w/order/submit", body)

    def order_cancel(self, body: dict) -> Any:
        return self._signed_post("/v2/auth/w/order/cancel", body)

    def cancel_all(self) -> Any:
        return self._signed_post("/v2/auth/w/order/cancel/multi", {"all": 1})

    def open_orders(self) -> Any:
        return self._signed_post("/v2/auth/r/orders", {})


# ---------------------------------------------------------------------------


class _CryptoBrokerBase:
    """Shared adapter body implementing the full ExecutionAdapter protocol.

    Subclasses set source_adapter and override _default_risk_verdict.
    """

    source_adapter: str = "_CryptoBrokerBase"

    _DEFAULT_SYMBOL_MAP: Dict[str, str] = {
        "BTCUSDT": "tBTCUSD",
        "BTC_USD": "tBTCUSD",
    }

    def __init__(
        self,
        run_id: str,
        *,
        transport: Optional[BitfinexTransport] = None,
        risk_check: Optional[Callable] = None,
        symbol_map: Optional[Dict[str, str]] = None,
    ) -> None:
        from execution.interfaces import OrderEvent  # local to avoid circular at module level
        self._run_id = run_id
        self._transport: BitfinexTransport = transport if transport is not None else BitfinexTransport()
        self._risk_check = risk_check
        self._symbol_map: Dict[str, str] = symbol_map if symbol_map is not None else dict(self._DEFAULT_SYMBOL_MAP)
        self._events: List[OrderEvent] = []
        self._pending: List[OrderEvent] = []
        self._latency_samples: list[LatencySample] = []
        self._last_submit_ns: int = 0
        self._last_ack_ns: int = 0
        self._position: float = 0.0
        self._cids_sent: set = set()
        self._order_cid_map: Dict[str, Dict[str, Any]] = {}  # order_id -> {cid, cid_date}
        self._reject_reasons: List[str] = []

    def _map_symbol(self, symbol: str) -> str:
        return self._symbol_map.get(symbol, f"t{symbol}")

    @property
    def reject_reasons(self) -> List[str]:
        return list(self._reject_reasons)

    # K1 single-submission-gate — THE one and only place self._transport.order_new is called.
    # grep-enforced: no other site in this module may call self._transport.order_new.
    def _send_order_new(self, body: dict) -> tuple[Any, int, int]:
        from execution import safety
        safety.record_crypto_order_call()
        submit_ns = time.perf_counter_ns()
        self._last_submit_ns = submit_ns
        try:
            response = self._transport.order_new(body)
        except Exception:
            ack_ns = time.perf_counter_ns()
            self._last_ack_ns = ack_ns
            raise
        ack_ns = time.perf_counter_ns()
        self._last_ack_ns = ack_ns
        return response, submit_ns, ack_ns

    def _default_risk_verdict(self) -> bool:
        return True

    def submit_order(self, order_intent) -> Any:
        from execution.interfaces import OrderEvent, OrderEventType
        import time as _time

        ts = order_intent.timestamp_ns
        cid = int.from_bytes(
            hashlib.sha256(order_intent.intent_id.encode()).digest()[:6], "big"
        )

        # Idempotency: reconnect-replay dedup (CRYPTO_LIVE.md §3.4)
        if cid in self._cids_sent:
            ev = OrderEvent(
                order_id=f"CRYPTO-CID-{cid}",
                intent_id=order_intent.intent_id,
                run_id=self._run_id,
                timestamp_ns=ts,
                receive_timestamp_ns=ts,
                event_type=OrderEventType.ORDER_REJECTED,
                symbol=order_intent.symbol,
                side=order_intent.side,
                price=order_intent.price,
                quantity=order_intent.quantity,
                rejection_reason="duplicate_cid",
                source_adapter=self.source_adapter,
            )
            self._events.append(ev)
            self._pending.append(ev)
            return ev

        # Risk gate — a crashed risk layer must never trade (fail-closed)
        try:
            allowed = (
                self._risk_check(order_intent)
                if self._risk_check is not None
                else self._default_risk_verdict()
            )
        except Exception as exc:
            allowed = False
            self.__dict__["_risk_rejection_reason"] = f"risk_check_error:{type(exc).__name__}"
        if not allowed:
            rejection_reason = getattr(self, "_risk_rejection_reason", "risk_blocked")
            ev = OrderEvent(
                order_id=f"CRYPTO-RISK-{cid}",
                intent_id=order_intent.intent_id,
                run_id=self._run_id,
                timestamp_ns=ts,
                receive_timestamp_ns=ts,
                event_type=OrderEventType.ORDER_REJECTED,
                symbol=order_intent.symbol,
                side=order_intent.side,
                price=order_intent.price,
                quantity=order_intent.quantity,
                rejection_reason=rejection_reason,
                source_adapter=self.source_adapter,
            )
            self._events.append(ev)
            self._pending.append(ev)
            return ev

        # Build Bitfinex order body
        venue_symbol = self._map_symbol(order_intent.symbol)
        amount = order_intent.quantity if order_intent.side.upper() == "BUY" else -order_intent.quantity
        bfx_body = {
            "type": "EXCHANGE LIMIT",
            "symbol": venue_symbol,
            "price": str(order_intent.price),
            "amount": str(amount),
            "cid": cid,
        }

        submitted_ev = OrderEvent(
            order_id=f"CRYPTO-{cid}",
            intent_id=order_intent.intent_id,
            run_id=self._run_id,
            timestamp_ns=ts,
            receive_timestamp_ns=ts,
            event_type=OrderEventType.ORDER_SUBMITTED,
            symbol=order_intent.symbol,
            side=order_intent.side,
            price=order_intent.price,
            quantity=order_intent.quantity,
            remaining_quantity=order_intent.quantity,
            source_adapter=self.source_adapter,
        )
        self._events.append(submitted_ev)
        self._pending.append(submitted_ev)

        wire_attempted = False
        try:
            wire_attempted = True
            _response, submit_ns, ack_ns = self._send_order_new(bfx_body)
            # counter counts venue ATTEMPTS; cid registry commits only on successful send
            # so failed sends are retryable; risk BLOCK advances neither.
            cid_date = time.strftime("%Y-%m-%d", time.gmtime())
            self._cids_sent.add(cid)
            self._order_cid_map[f"CRYPTO-{cid}"] = {"cid": cid, "cid_date": cid_date}
        except (CryptoRateLimitError, CryptoTransportError) as exc:
            if not wire_attempted:
                raise
            sample = LatencySample(
                intent_id=order_intent.intent_id,
                cid=cid,
                submit_ns=self._last_submit_ns,
                ack_ns=self._last_ack_ns,
                accepted=False,
            )
            self._latency_samples.append(sample)
            rejection_reason = f"{type(exc).__name__}: {exc}"[:240]
            self._reject_reasons.append(rejection_reason)
            rej_ev = OrderEvent(
                order_id=f"CRYPTO-{cid}",
                intent_id=order_intent.intent_id,
                run_id=self._run_id,
                timestamp_ns=ts,
                receive_timestamp_ns=ts,
                event_type=OrderEventType.ORDER_REJECTED,
                symbol=order_intent.symbol,
                side=order_intent.side,
                price=order_intent.price,
                quantity=order_intent.quantity,
                rejection_reason=rejection_reason,
                source_adapter=self.source_adapter,
            )
            self._events.append(rej_ev)
            self._pending.append(rej_ev)
            return rej_ev

        sample = LatencySample(
            intent_id=order_intent.intent_id,
            cid=cid,
            submit_ns=submit_ns,
            ack_ns=ack_ns,
            accepted=True,
        )
        self._latency_samples.append(sample)
        accepted_ev = OrderEvent(
            order_id=f"CRYPTO-{cid}",
            intent_id=order_intent.intent_id,
            run_id=self._run_id,
            timestamp_ns=ts,
            receive_timestamp_ns=ts,
            event_type=OrderEventType.ORDER_ACCEPTED,
            symbol=order_intent.symbol,
            side=order_intent.side,
            price=order_intent.price,
            quantity=order_intent.quantity,
            remaining_quantity=order_intent.quantity,
            latency_ms=sample.latency_ms,
            source_adapter=self.source_adapter,
        )
        self._events.append(accepted_ev)
        self._pending.append(accepted_ev)
        return accepted_ev

    def cancel_order(self, order_id: str) -> Any:
        from execution.interfaces import OrderEvent, OrderEventType
        cid_info = self._order_cid_map.get(order_id)
        try:
            if cid_info is not None:
                self._transport.order_cancel({"cid": cid_info["cid"], "cid_date": cid_info["cid_date"]})
            else:
                self._transport.order_cancel({"cid": order_id, "cid_date": time.strftime("%Y-%m-%d", time.gmtime())})
        except (CryptoRateLimitError, CryptoTransportError) as exc:
            ev = OrderEvent(
                order_id=order_id,
                intent_id="",
                run_id=self._run_id,
                timestamp_ns=0,
                receive_timestamp_ns=0,
                event_type=OrderEventType.ORDER_CANCEL_REQUESTED,
                symbol="",
                side="",
                price=0.0,
                quantity=0.0,
                rejection_reason=f"cancel_failed:{type(exc).__name__}",
                source_adapter=self.source_adapter,
            )
            self._events.append(ev)
            self._pending.append(ev)
            return ev
        ev = OrderEvent(
            order_id=order_id,
            intent_id="",
            run_id=self._run_id,
            timestamp_ns=0,
            receive_timestamp_ns=0,
            event_type=OrderEventType.ORDER_CANCELLED,
            symbol="",
            side="",
            price=0.0,
            quantity=0.0,
            source_adapter=self.source_adapter,
        )
        self._events.append(ev)
        self._pending.append(ev)
        return ev

    def replace_order(self, order_id: str, new_order_intent) -> Any:
        self.cancel_order(order_id)
        return self.submit_order(new_order_intent)

    def get_order_status(self, order_id: str) -> Any:
        for ev in reversed(self._events):
            if ev.order_id == order_id:
                return ev
        return None

    def get_position(self, symbol: str) -> float:
        del symbol
        return self._position

    def get_account_state(self) -> Any:
        from execution.interfaces import AccountState
        return AccountState(position=self._position)

    def drain_order_events(self) -> List:
        out = list(self._pending)
        self._pending.clear()
        return out

    def drain_latency_samples(self) -> list[LatencySample]:
        out = list(self._latency_samples)
        self._latency_samples.clear()
        return out

    def after_elapse(self, replay_time_ns: int) -> None:
        del replay_time_ns

    def reconcile(self) -> dict:
        """Diff venue open orders against local _pending by cid.

        Returns surface for C7/C8 reconnect reconciliation per CRYPTO_LIVE.md §3.4.
        """
        try:
            venue_orders = self._transport.open_orders()
        except (CryptoRateLimitError, CryptoTransportError):
            venue_orders = []
        venue_cids: set = set()
        if isinstance(venue_orders, list):
            for o in venue_orders:
                if isinstance(o, (list, tuple)) and len(o) > 2:
                    cid_val = o[2]
                    if cid_val:
                        venue_cids.add(int(cid_val))
        local_cids = set(self._cids_sent)
        unknown_at_venue = sorted(local_cids - venue_cids)
        missing_locally = sorted(venue_cids - local_cids)
        return {
            "venue_open": len(venue_cids),
            "local_pending": len(local_cids),
            "unknown_at_venue": unknown_at_venue,
            "missing_locally": missing_locally,
        }

    def cancel_all_open(self) -> bool:
        """Cancel all open orders via transport cancel_all. Returns True on success.

        Kill-sequence callers must use cancel_all_open directly and check the bool;
        close is best-effort teardown only.
        """
        import sys
        try:
            self._transport.cancel_all()
            return True
        except CryptoRateLimitError as exc:
            print(f"[{self.source_adapter}] cancel_all_open: {type(exc).__name__}", file=sys.stderr)
            return False
        except CryptoTransportError as exc:
            print(f"[{self.source_adapter}] cancel_all_open: {type(exc).__name__}", file=sys.stderr)
            return False
        except Exception as exc:
            print(f"[{self.source_adapter}] cancel_all_open: {type(exc).__name__}", file=sys.stderr)
            return False

    def close(self) -> None:
        """Best-effort cancel-all on disconnect.

        REST has no dead-man switch; WebSocket DMS lands with the C8 order channel.
        This is the cancel-on-disconnect complement for REST sessions only.
        Kill-sequence callers must use cancel_all_open directly and check the bool;
        close is best-effort teardown only.
        """
        self.cancel_all_open()  # result intentionally ignored at close


# ---------------------------------------------------------------------------


class CryptoPaperBrokerAdapter(_CryptoBrokerBase):
    """Bitfinex paper sub-account adapter (same API surface, paper key).

    Plumbing allowed without risk layer — _default_risk_verdict returns True.
    """

    source_adapter = "CryptoPaperBrokerAdapter"

    def __init__(self, run_id: str = "crypto_paper", **kwargs) -> None:
        super().__init__(run_id, **kwargs)

    def _default_risk_verdict(self) -> bool:
        return True


class CryptoLiveBrokerAdapter(_CryptoBrokerBase):
    """Bitfinex live broker adapter.

    Fail-closed until C7 wires TradeManagerRiskLayer — mirrors live_broker.py stub
    philosophy: live never trades without an explicit risk callable.
    _default_risk_verdict returns False with rejection_reason="no_risk_layer_wired".
    """

    source_adapter = "CryptoLiveBrokerAdapter"
    _risk_rejection_reason = "no_risk_layer_wired"

    def __init__(self, run_id: str = "crypto_live", **kwargs) -> None:
        super().__init__(run_id, **kwargs)

    def _default_risk_verdict(self) -> bool:
        return False
