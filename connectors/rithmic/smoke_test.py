from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAPER_CONFIG = REPO_ROOT / "packages" / "data_system" / "config" / "rithmic_api_paper.yaml"
DEFAULT_RAW_ROOT = REPO_ROOT / "data" / "rithmic" / "paper" / "raw"
DEFAULT_REPORT_ROOT = REPO_ROOT / "reports" / "rithmic_paper"

BAD_PAPER_ENDPOINT_TOKENS = (
    "rituz00100",
    "rithmic_uat",
    "uat_dmz",
    "orangeburg",
)

LAST_TRADE_BIT = 1
BBO_BIT = 2
ORDER_BOOK_BIT = 4
DATA_TYPE_NAMES = {
    LAST_TRADE_BIT: "LAST_TRADE",
    BBO_BIT: "BBO",
    ORDER_BOOK_BIT: "ORDER_BOOK",
}


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class SmokeBlockedError(RuntimeError):
    def __init__(self, blockers: list[Blocker]) -> None:
        self.blockers = blockers
        super().__init__(", ".join(blocker.code for blocker in blockers))


@dataclass
class SmokeSettings:
    env: str
    symbol_root: str
    exchange: str
    duration_sec: float
    config_path: Path
    raw_root: Path = DEFAULT_RAW_ROOT
    report_root: Path = DEFAULT_REPORT_ROOT
    url: str = ""
    system_name: str = "Rithmic Paper Trading"
    gateway: str = "Chicago Area"
    app_name: str = "HFT3"
    app_version: str = "1.0"
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    allow_symbol_fallback: bool = False
    reconnect_test: bool = False

    @property
    def credentials_present(self) -> dict[str, bool]:
        return {
            "username_set": bool(self.username),
            "password_set": bool(self.password),
            "redacted": True,
        }

    def redacted_diagnostics(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "system_name": self.system_name,
            "gateway": self.gateway,
            "config_path": str(self.config_path),
            "url_set": bool(self.url),
            "credentials": self.credentials_present,
            "symbol_root": self.symbol_root,
            "exchange": self.exchange,
            "duration_sec": self.duration_sec,
            "raw_root": str(self.raw_root),
            "report_root": str(self.report_root),
        }

    def blockers(self) -> list[Blocker]:
        blockers: list[Blocker] = []
        env = self.env.strip().lower()
        system = self.system_name.strip().lower()
        gateway = self.gateway.strip().lower()
        url = self.url.strip().lower()

        if env != "paper":
            blockers.append(Blocker("UNSUPPORTED_ENV", "This smoke command is scoped to --env paper."))
        if "paper" not in system:
            blockers.append(
                Blocker(
                    "PAPER_SYSTEM_NAME_MISSING",
                    "Paper smoke requires System Name 'Rithmic Paper Trading'.",
                )
            )
        if "test" in system:
            blockers.append(
                Blocker(
                    "PAPER_PROFILE_POINTS_TO_TEST",
                    "Paper smoke cannot use Rithmic Test as the system name.",
                )
            )
        if "chicago" not in gateway:
            blockers.append(
                Blocker(
                    "PAPER_CHICAGO_GATEWAY_MISSING",
                    "Paper smoke requires a Chicago gateway/area label.",
                )
            )
        if "orangeburg" in gateway:
            blockers.append(
                Blocker(
                    "PAPER_PROFILE_POINTS_TO_ORANGEBURG",
                    "Paper smoke cannot use Orangeburg as the gateway.",
                )
            )
        if not url:
            blockers.append(
                Blocker(
                    "PAPER_PROTOCOL_URL_MISSING",
                    "Set RITHMIC_PROTOCOL_URL or protocol.url in the Paper profile.",
                )
            )
        elif any(token in url for token in BAD_PAPER_ENDPOINT_TOKENS):
            blockers.append(
                Blocker(
                    "PAPER_PROTOCOL_ENDPOINT_IS_TEST_OR_ORANGEBURG",
                    "Paper smoke cannot use Test/Orangeburg/UAT endpoint strings.",
                )
            )
        if not self.username:
            blockers.append(Blocker("RITHMIC_USERNAME_MISSING", "Set RITHMIC_USERNAME at runtime."))
        if not self.password:
            blockers.append(Blocker("RITHMIC_PASSWORD_MISSING", "Set RITHMIC_PASSWORD at runtime."))
        return blockers


class JsonlMarketDataRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.counts: dict[str, int] = {}
        self.first_receive_utc: str = ""
        self.last_receive_utc: str = ""
        self.max_receive_gap_ms: float = 0.0
        self._last_receive_monotonic_ns: int | None = None

    def append(
        self,
        *,
        message_type: str,
        symbol: str,
        exchange: str,
        payload: Any,
    ) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc).isoformat()
        now_mono = time.monotonic_ns()
        if not self.first_receive_utc:
            self.first_receive_utc = now_utc
        self.last_receive_utc = now_utc
        if self._last_receive_monotonic_ns is not None:
            gap_ms = (now_mono - self._last_receive_monotonic_ns) / 1_000_000
            self.max_receive_gap_ms = max(self.max_receive_gap_ms, gap_ms)
        self._last_receive_monotonic_ns = now_mono

        safe_payload = _to_jsonable(payload)
        record = {
            "receive_timestamp_utc": now_utc,
            "receive_monotonic_ns": now_mono,
            "exchange_timestamp": _extract_exchange_timestamp(safe_payload),
            "symbol": symbol,
            "exchange": exchange,
            "message_type": message_type,
            "bid_price": _pick_number(safe_payload, ("bid_price", "bid", "best_bid_price")),
            "ask_price": _pick_number(safe_payload, ("ask_price", "ask", "best_ask_price")),
            "trade_price": _pick_number(safe_payload, ("trade_price", "price", "last_price")),
            "size": _pick_number(safe_payload, ("size", "trade_size", "last_size")),
            "raw_payload": safe_payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self.counts[message_type] = self.counts.get(message_type, 0) + 1
        return record

    def summary(self) -> dict[str, Any]:
        total = sum(self.counts.values())
        return {
            "path": str(self.path),
            "message_counts": dict(sorted(self.counts.items())),
            "total_messages": total,
            "first_receive_utc": self.first_receive_utc,
            "last_receive_utc": self.last_receive_utc,
            "max_receive_gap_ms": self.max_receive_gap_ms,
        }


class RithmicProtocolSmokeRunner:
    def __init__(
        self,
        settings: SmokeSettings,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._recorder: JsonlMarketDataRecorder | None = None
        self._symbol = ""
        self._exchange = settings.exchange

    async def run(self) -> dict[str, Any]:
        blockers = self.settings.blockers()
        if blockers:
            raise SmokeBlockedError(blockers)

        runtime = _load_async_rithmic() if self._client_factory is None else {}
        client_cls = self._client_factory or runtime["RithmicClient"]
        client = client_cls(
            user=self.settings.username,
            password=self.settings.password,
            system_name=self.settings.system_name,
            app_name=self.settings.app_name,
            app_version=self.settings.app_version,
            url=self.settings.url,
        )
        _attach_handler(client, "on_tick", self._on_tick)
        _attach_handler(client, "on_order_book", self._on_order_book)
        _attach_handler(client, "on_market_depth", self._on_market_depth)

        started = datetime.now(timezone.utc)
        connected = False
        entitlement_status: dict[str, Any] = {"status": "not_observed", "exchange": self.settings.exchange}
        front_month = ""
        subscribe_status: dict[str, Any] = {"status": "not_started"}
        reconnect_status: dict[str, Any] = {"status": "not_requested"}
        try:
            await client.connect(plants=[_ticker_plant(runtime)])
            connected = True
            entitlement_status = await self._exchange_entitlement_status(client)
            if entitlement_status.get("status") != "pass":
                raise RuntimeError(f"EXCHANGE_ENTITLEMENT_BLOCKED: {entitlement_status.get('reason')}")

            front_month = await self._resolve_front_month(client)
            self._symbol = front_month
            raw_path = _raw_path(self.settings.raw_root, front_month, started)
            self._recorder = JsonlMarketDataRecorder(raw_path)

            update_bits = _market_data_bits(runtime)
            await client.subscribe_to_market_data(front_month, self.settings.exchange, update_bits)
            subscribe_status = {
                "status": "subscribed",
                "data_types": ["LAST_TRADE", "BBO", "ORDER_BOOK"],
                "update_bits": update_bits,
            }
            await asyncio.sleep(max(self.settings.duration_sec, 0.0))
            if self.settings.reconnect_test:
                reconnect_status = await _reconnect_probe(client, runtime)
        finally:
            if connected:
                await _maybe_await(client.disconnect())

        finished = datetime.now(timezone.utc)
        recorder_summary = self._recorder.summary() if self._recorder else {}
        counts = recorder_summary.get("message_counts", {})
        blockers_after_run: list[Blocker] = []
        if not counts.get("LAST_TRADE"):
            blockers_after_run.append(Blocker("LAST_TRADE_NOT_OBSERVED", "No LAST_TRADE messages arrived."))
        if not counts.get("BBO"):
            blockers_after_run.append(Blocker("BBO_NOT_OBSERVED", "No BBO messages arrived."))
        if not self._recorder or not self._recorder.path.is_file():
            blockers_after_run.append(Blocker("RAW_JSONL_NOT_WRITTEN", "Raw JSONL market data file was not written."))

        status = "pass" if not blockers_after_run else "fail"
        report = {
            "status": status,
            "run_started_utc": started.isoformat(),
            "run_finished_utc": finished.isoformat(),
            "elapsed_sec": (finished - started).total_seconds(),
            "gui_independent": True,
            "mode": "headless_market_data_only",
            "environment": {
                "env": self.settings.env,
                "system_name": self.settings.system_name,
                "gateway": self.settings.gateway,
                "url_set": bool(self.settings.url),
                "config_path": str(self.settings.config_path),
                "credentials": self.settings.credentials_present,
            },
            "symbol": {
                "root": self.settings.symbol_root,
                "exchange": self.settings.exchange,
                "front_month": front_month,
                "fallback_used": front_month == self.settings.symbol_root,
            },
            "exchange_permissions": entitlement_status,
            "subscription": subscribe_status,
            "market_data": recorder_summary,
            "proof_of_life": status == "pass",
            "reconnect_test": reconnect_status,
            "blockers": [blocker.to_dict() for blocker in blockers_after_run],
        }
        report_path = _report_path(self.settings.report_root, started)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["report_path"] = str(report_path)
        return report

    async def _resolve_front_month(self, client: Any) -> str:
        try:
            symbol = await client.get_front_month_contract(self.settings.symbol_root, self.settings.exchange)
        except Exception:
            if not self.settings.allow_symbol_fallback:
                raise
            return self.settings.symbol_root
        symbol = str(symbol or "").strip()
        if not symbol:
            if not self.settings.allow_symbol_fallback:
                raise RuntimeError("FRONT_MONTH_EMPTY")
            return self.settings.symbol_root
        return symbol

    async def _exchange_entitlement_status(self, client: Any) -> dict[str, Any]:
        try:
            exchanges = await client.list_exchanges()
        except Exception as exc:
            return {
                "status": "fail",
                "exchange": self.settings.exchange,
                "reason": f"LIST_EXCHANGES_FAILED: {_safe_error(exc, self.settings)}",
            }
        names = _exchange_names(exchanges)
        if not names:
            return {
                "status": "fail",
                "exchange": self.settings.exchange,
                "reason": "NO_EXCHANGES_RETURNED",
                "observed": [],
            }
        wanted = self.settings.exchange.upper()
        matched = any(name.upper() == wanted for name in names)
        return {
            "status": "pass" if matched else "fail",
            "exchange": self.settings.exchange,
            "reason": "" if matched else "EXCHANGE_NOT_ENTITLED_OR_NOT_LISTED",
            "observed": sorted(names)[:100],
        }

    async def _on_tick(self, payload: Any) -> None:
        if not self._recorder:
            return
        message_type = _message_type_from_tick(payload)
        self._recorder.append(
            message_type=message_type,
            symbol=self._symbol,
            exchange=self._exchange,
            payload=payload,
        )

    async def _on_order_book(self, payload: Any) -> None:
        if not self._recorder:
            return
        self._recorder.append(
            message_type="ORDER_BOOK",
            symbol=self._symbol,
            exchange=self._exchange,
            payload=payload,
        )

    async def _on_market_depth(self, payload: Any) -> None:
        if not self._recorder:
            return
        self._recorder.append(
            message_type="MARKET_DEPTH",
            symbol=self._symbol,
            exchange=self._exchange,
            payload=payload,
        )


def settings_from_args(args: argparse.Namespace, environ: dict[str, str] | None = None) -> SmokeSettings:
    env = environ if environ is not None else os.environ
    config_path = Path(args.config or DEFAULT_PAPER_CONFIG).resolve()
    cfg = _load_yaml(config_path)
    protocol = dict(cfg.get("protocol") or {})
    raw_root = Path(args.raw_root or DEFAULT_RAW_ROOT).resolve()
    report_root = Path(args.report_root or DEFAULT_REPORT_ROOT).resolve()
    return SmokeSettings(
        env=args.env,
        symbol_root=args.symbol_root,
        exchange=args.exchange,
        duration_sec=float(args.duration),
        config_path=config_path,
        raw_root=raw_root,
        report_root=report_root,
        url=(
            args.url
            or env.get("RITHMIC_PROTOCOL_URL")
            or env.get("RITHMIC_URL")
            or str(protocol.get("url") or "")
        ).strip(),
        system_name=(
            env.get("RITHMIC_SYSTEM_NAME")
            or env.get("RITHMIC_API_SYSTEM")
            or str(protocol.get("system_name") or cfg.get("system") or "Rithmic Paper Trading")
        ).strip(),
        gateway=(
            env.get("RITHMIC_GATEWAY")
            or env.get("RITHMIC_API_GATEWAY")
            or str(protocol.get("gateway") or cfg.get("gateway") or "Chicago Area")
        ).strip(),
        app_name=(env.get("RITHMIC_APP_NAME") or str(protocol.get("app_name") or "HFT3")).strip(),
        app_version=(env.get("RITHMIC_APP_VERSION") or str(protocol.get("app_version") or "1.0")).strip(),
        username=(env.get("RITHMIC_USERNAME") or env.get("RITHMIC_USER") or "").strip(),
        password=(env.get("RITHMIC_PASSWORD") or "").strip(),
        allow_symbol_fallback=bool(args.allow_symbol_fallback),
        reconnect_test=bool(args.reconnect_test),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headless Rithmic Paper market-data smoke test")
    parser.add_argument("--env", default="paper", help="Only 'paper' is supported for this command.")
    parser.add_argument("--symbol-root", default="ES")
    parser.add_argument("--exchange", default="CME")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--url", default="", help="Runtime websocket URL override; prefer RITHMIC_PROTOCOL_URL.")
    parser.add_argument("--raw-root", default=str(DEFAULT_RAW_ROOT))
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--allow-symbol-fallback", action="store_true")
    parser.add_argument("--reconnect-test", action="store_true")
    parser.add_argument("--check-config", action="store_true", help="Validate settings without connecting.")
    return parser


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = settings_from_args(args, environ)
    blockers = settings.blockers()
    if blockers:
        _print_blocked(settings, blockers)
        return 2
    if args.check_config:
        print(json.dumps({"status": "ready_to_connect", "diagnostics": settings.redacted_diagnostics()}, indent=2))
        return 0
    try:
        report = asyncio.run(RithmicProtocolSmokeRunner(settings).run())
    except SmokeBlockedError as exc:
        _print_blocked(settings, exc.blockers)
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "fail",
                    "reason_code": "RITHMIC_PAPER_SMOKE_FAILED",
                    "error": _safe_error(exc, settings),
                    "diagnostics": settings.redacted_diagnostics(),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 1


def _print_blocked(settings: SmokeSettings, blockers: list[Blocker]) -> None:
    print(
        json.dumps(
            {
                "status": "blocked",
                "reason_code": "RITHMIC_PAPER_SMOKE_BLOCKED",
                "blockers": [blocker.to_dict() for blocker in blockers],
                "diagnostics": settings.redacted_diagnostics(),
            },
            indent=2,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_async_rithmic() -> dict[str, Any]:
    try:
        from async_rithmic import DataType, RithmicClient, SysInfraType
    except Exception as exc:  # pragma: no cover - depends on operator environment
        raise RuntimeError("ASYNC_RITHMIC_NOT_AVAILABLE") from exc
    return {
        "DataType": DataType,
        "RithmicClient": RithmicClient,
        "SysInfraType": SysInfraType,
    }


def _ticker_plant(runtime: dict[str, Any]) -> Any:
    sys_infra = runtime.get("SysInfraType")
    if sys_infra is None:
        return "TICKER_PLANT"
    return sys_infra.TICKER_PLANT


def _market_data_bits(runtime: dict[str, Any]) -> int:
    data_type = runtime.get("DataType")
    if data_type is None:
        return LAST_TRADE_BIT | BBO_BIT | ORDER_BOOK_BIT
    return int(data_type.LAST_TRADE) | int(data_type.BBO) | int(data_type.ORDER_BOOK)


def _attach_handler(client: Any, event_name: str, handler: Callable[[Any], Any]) -> None:
    event = getattr(client, event_name)
    event += handler
    setattr(client, event_name, event)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _reconnect_probe(client: Any, runtime: dict[str, Any]) -> dict[str, Any]:
    try:
        await _maybe_await(client.disconnect())
        await _maybe_await(client.connect(plants=[_ticker_plant(runtime)]))
    except Exception as exc:
        return {"status": "fail", "reason": f"RECONNECT_FAILED: {exc.__class__.__name__}"}
    return {"status": "pass"}


def _raw_path(raw_root: Path, symbol: str, started: datetime) -> Path:
    return raw_root / started.strftime("%Y-%m-%d") / f"{_safe_filename(symbol)}.jsonl"


def _report_path(report_root: Path, started: datetime) -> Path:
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    return report_root / started.strftime("%Y-%m-%d") / f"{run_id}_smoke_report.json"


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value) or "symbol"


def _message_type_from_tick(payload: Any) -> str:
    data = _to_jsonable(payload)
    if isinstance(data, dict):
        raw = data.get("data_type")
        if isinstance(raw, str) and raw:
            return raw.split(".")[-1]
        if isinstance(raw, int):
            return DATA_TYPE_NAMES.get(raw, f"DATA_TYPE_{raw}")
    raw_type = getattr(payload, "data_type", None)
    name = getattr(raw_type, "name", None)
    if name:
        return str(name)
    try:
        return DATA_TYPE_NAMES.get(int(raw_type), f"DATA_TYPE_{int(raw_type)}")
    except Exception:
        return "TICK"


def _exchange_names(payload: Any) -> set[str]:
    data = _to_jsonable(payload)
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"exchange", "exchange_name", "exch", "code"} and item:
                    names.add(str(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return names


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    descriptor = getattr(value, "DESCRIPTOR", None)
    if descriptor is not None:
        try:
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(value, preserving_proto_field_name=True)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def _pick_number(payload: Any, keys: tuple[str, ...]) -> float | int | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _extract_exchange_timestamp(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("datetime", "exchange_timestamp", "timestamp", "symbol_exchange_timestamp"):
        value = payload.get(key)
        if value:
            return str(value)
    ssboe = payload.get("ssboe")
    usecs = payload.get("usecs", 0)
    if isinstance(ssboe, int):
        try:
            return datetime.fromtimestamp(ssboe + (int(usecs) / 1_000_000), timezone.utc).isoformat()
        except Exception:
            return str(ssboe)
    return ""


def _safe_error(exc: Exception, settings: SmokeSettings) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    for secret in (settings.username, settings.password):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


if __name__ == "__main__":
    raise SystemExit(main())
