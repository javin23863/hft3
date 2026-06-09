#!/usr/bin/env python3
"""Minimal Rithmic historical-data downloader proof.

Connects to Rithmic API / History Plant, pulls a small window of historical
ticks, saves to local Parquet + manifest JSON.  Does NOT use R|Trader Pro GUI,
screen scraping, or any live/execution path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.rithmic_trial.platform import is_windows


# ---------------------------------------------------------------------------
# environment helpers
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _rithmic_credentials() -> dict[str, str]:
    return {
        "user": _env("RITHMIC_USER") or _env("RITHMIC_USERNAME"),
        "password": _env("RITHMIC_PASSWORD"),
        "system_name": _env("RITHMIC_SYSTEM_NAME")
        or _env("RITHMIC_GATEWAY", "RITHMIC_PAPER"),
        "app_name": _env("RITHMIC_APP_NAME", "HFT3"),
        "app_version": _env("RITHMIC_APP_VERSION", "1.0"),
        "url": _env("RITHMIC_URL")
        or _env("HFT3_RITHMIC_HOST", "wss://rituz00100.00.rithmic.com:443"),
    }


def _validate_credentials(creds: dict[str, str]) -> list[str]:
    missing = [k for k in ("user", "password") if not creds.get(k)]
    return missing


# ---------------------------------------------------------------------------
# data helpers
# ---------------------------------------------------------------------------


def _infer_data_label(schema: pa.Schema) -> str:
    """Heuristic: return 'ticks', 'bars', 'depth', or 'mbo' from column names."""
    fields = {f.name.lower() for f in schema}

    order_fields = {"order_id", "action", "side", "flags"}
    depth_fields = {"bid_price", "ask_price", "bid_size", "ask_size", "bid_px", "ask_px"}
    bar_fields = {"open", "high", "low", "close", "volume"}

    if order_fields.issubset(fields) or ("order_id" in fields and "price" in fields):
        if depth_fields & fields:
            return "mbo"
        return "ticks"
    if bar_fields & fields:
        return "bars"
    if depth_fields & fields:
        return "depth/mbp"
    if "price" in fields and "size" in fields:
        return "ticks"
    return "unknown"


def _records_to_table(records: list[dict[str, Any]]) -> pa.Table:
    if not records:
        return pa.table({})
    keys = list(records[0].keys())
    columns: dict[str, list[Any]] = {k: [] for k in keys}
    for r in records:
        for k in keys:
            columns[k].append(r.get(k))
    arrays = [pa.array(columns[k]) for k in keys]
    return pa.table(dict(zip(keys, arrays)))


def _extract_first_timestamp(records: list[dict[str, Any]]) -> datetime | None:
    for key in ("timestamp", "ts", "datetime", "date", "time", "utc", "trade_time"):
        if records and key in records[0]:
            val = records[0][key]
            if isinstance(val, datetime):
                return val
            if isinstance(val, (int, float)):
                if val > 1e12:
                    return datetime.fromtimestamp(val / 1e3, tz=timezone.utc)
                if val > 1e9:
                    return datetime.fromtimestamp(val, tz=timezone.utc)
    return None


def _extract_last_timestamp(records: list[dict[str, Any]]) -> datetime | None:
    for key in ("timestamp", "ts", "datetime", "date", "time", "utc", "trade_time"):
        if records and key in records[-1]:
            val = records[-1][key]
            if isinstance(val, datetime):
                return val
            if isinstance(val, (int, float)):
                if val > 1e12:
                    return datetime.fromtimestamp(val / 1e3, tz=timezone.utc)
                if val > 1e9:
                    return datetime.fromtimestamp(val, tz=timezone.utc)
    return None


# ---------------------------------------------------------------------------
# Rithmic client wrapper
# ---------------------------------------------------------------------------


class RithmicHistoricalClient:
    """Thin wrapper around async_rithmic for historical tick/bar retrieval.

    async_rithmic 1.6.1 API notes:
      * RithmicClient(url=...) is required; url is the gateway websocket, e.g.
        "wss://rituz00100.00.rithmic.com:443".
      * system_name is the Rithmic *system* (e.g. "RITHMIC_PAPER"), not a host.
      * get_historical_tick_data / get_historical_time_bars take start_time,
        end_time, idle_timeout, max_pages.  Pagination is handled inside the
        library; we just pass max_pages.
      * The bundled `rithmic_ssl_cert_auth_params` ships USERTrust RSA
        Certification Authority as the trust anchor, but the current Rithmic
        server chain is signed by Sectigo Public Server Authentication CA DV
        R36.  Pass ``ssl_ca_file`` to inject the right intermediate; the
        public Rithmic intermediate is at
        http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt.
    """

    def __init__(
        self,
        creds: dict[str, str],
        url: str,
        *,
        ssl_ca_file: str | None = None,
        insecure_skip_verify: bool = False,
    ) -> None:
        self._creds = creds
        self._url = url
        self._ssl_ca_file = ssl_ca_file
        self._insecure_skip_verify = insecure_skip_verify
        self._client: Any = None
        self.connected = False

    def _build_ssl_context(self) -> Any:
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if self._insecure_skip_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.set_default_verify_paths()
            if self._ssl_ca_file:
                ctx.load_verify_locations(self._ssl_ca_file)
        return ctx

    async def connect(self) -> None:
        from async_rithmic import RithmicClient

        self._client = RithmicClient(
            user=self._creds["user"],
            password=self._creds["password"],
            system_name=self._creds["system_name"],
            app_name=self._creds["app_name"],
            app_version=self._creds["app_version"],
            url=self._url,
        )
        # Override the library's bundled cert with our chain-aware context.
        self._client.ssl_context = self._build_ssl_context()
        await self._client.connect()
        self.connected = True

    async def list_system_names(self) -> list[str]:
        """Discover valid system_name values via get_system_info().

        Returns a list of system_name strings.  If the request fails for
        any reason, raises with the underlying error.
        """
        if not self.connected:
            raise RuntimeError("Not connected")
        info = await self._client.get_system_info()
        names = list(getattr(info, "system_name", []) or [])
        return names

    async def get_historical_ticks(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        max_pages: int = 1,
    ) -> list[dict[str, Any]]:
        if not self.connected:
            raise RuntimeError("Not connected")
        ticks = await self._client.get_historical_tick_data(
            symbol=symbol,
            exchange=exchange,
            start_time=start_time,
            end_time=end_time,
            max_pages=max_pages,
        )
        return self._normalize_records(ticks)

    async def get_historical_bars(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        bar_type: int = 2,
        bar_type_periods: int = 1,
        max_pages: int = 1,
    ) -> list[dict[str, Any]]:
        if not self.connected:
            raise RuntimeError("Not connected")
        from async_rithmic.plants.history import TimeBarType

        bars = await self._client.get_historical_time_bars(
            symbol=symbol,
            exchange=exchange,
            start_time=start_time,
            end_time=end_time,
            bar_type=TimeBarType.Value(_BAR_TYPE_NAMES[bar_type]),
            bar_type_periods=bar_type_periods,
            max_pages=max_pages,
        )
        return self._normalize_records(bars)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self.connected = False

    @staticmethod
    def _normalize_records(
        records: list[Any],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in records:
            if hasattr(r, "_asdict"):
                d = r._asdict()
            elif hasattr(r, "__dict__"):
                d = {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
            elif isinstance(r, dict):
                d = r
            else:
                d = {"raw": str(r)}
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------

MAX_PAGES_DEFAULT = 200

_BAR_TYPE_NAMES = {
    0: "BARTYPE_UNSPECIFIED",
    1: "SECOND_BAR",
    2: "MINUTE_BAR",
    3: "DAILY_BAR",
    4: "WEEKLY_BAR",
}


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def _output_dir(
    repo_root: Path,
    symbol: str,
    dt: datetime,
    base: str = "data/raw/rithmic_test",
) -> Path:
    date_str = dt.strftime("%Y-%m-%d")
    return repo_root / base / f"symbol={symbol}" / f"date={date_str}"


def _save_parquet(table: pa.Table, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ticks.parquet"
    pq.write_table(table, str(path))
    return path


def _write_manifest(
    out_dir: Path,
    *,
    source: str,
    symbol: str,
    exchange: str,
    request_start: str,
    request_end: str,
    data_label: str,
    row_count: int,
    first_timestamp: str | None,
    last_timestamp: str | None,
    columns: list[str],
    api_env: str,
    error: str | None = None,
) -> Path:
    manifest: dict[str, Any] = {
        "source": source,
        "symbol": symbol,
        "exchange": exchange,
        "data_label": data_label,
        "request_start": request_start,
        "request_end": request_end,
        "row_count": row_count,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "returned_columns": columns,
        "api_url_or_env_name": api_env,
        "download_time_utc": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        manifest["error"] = error

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal Rithmic historical-data download proof"
    )
    parser.add_argument(
        "--symbol",
        default=os.environ.get("RITHMIC_SYMBOL", ""),
        help="Contract symbol (e.g. ESM5, MES).  Default: RITHMIC_SYMBOL env",
    )
    parser.add_argument(
        "--exchange",
        default="CME",
        help="Exchange (default: CME)",
    )
    parser.add_argument(
        "--start",
        default="",
        help="Start UTC ISO-8601.  Default: 1 minute ago",
    )
    parser.add_argument(
        "--end",
        default="",
        help="End UTC ISO-8601.  Default: now",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=1,
        help="Window size in minutes when --start is omitted (default: 1)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES_DEFAULT,
        help=f"Max pagination pages (default: {MAX_PAGES_DEFAULT})",
    )
    parser.add_argument(
        "--data-type",
        choices=("ticks", "bars"),
        default="ticks",
        help="Historical data type to request (default: ticks)",
    )
    parser.add_argument(
        "--bar-type",
        type=int,
        default=2,
        help="Time bar type code (0=UNSPECIFIED, 1=SECOND, 2=MINUTE, 3=DAILY, 4=WEEKLY; default: 2)",
    )
    parser.add_argument(
        "--bar-size",
        type=int,
        default=1,
        help="Bar size in bar-type units (default: 1)",
    )
    parser.add_argument(
        "--output-base",
        default="data/raw/rithmic_test",
        help="Output base dir relative to repo root",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Connect and list valid system_name values; do not request data",
    )
    parser.add_argument(
        "--ssl-ca-file",
        default="",
        help="Path to PEM bundle with the Rithmic server CA chain (overrides bundled cert)",
    )
    parser.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        help="Disable TLS verification (DEV ONLY — does not authenticate the server)",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    # credentials
    creds = _rithmic_credentials()
    missing = _validate_credentials(creds)
    if missing:
        print(f"MISSING CREDENTIALS: {', '.join(missing)}")
        print("Set RITHMIC_USER / RITHMIC_PASSWORD (or RITHMIC_USERNAME / RITHMIC_PASSWORD)")
        return 1

    # time window
    now = datetime.now(timezone.utc)
    if args.start:
        start_utc = datetime.fromisoformat(args.start)
    else:
        start_utc = now - timedelta(minutes=args.window_minutes)
    if args.end:
        end_utc = datetime.fromisoformat(args.end)
    else:
        end_utc = now

    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)

    # connect
    client = RithmicHistoricalClient(
        creds,
        url=creds["url"],
        ssl_ca_file=args.ssl_ca_file or None,
        insecure_skip_verify=args.insecure_skip_verify,
    )
    try:
        await client.connect()
    except ImportError as exc:
        print(f"DEPENDENCY MISSING: {exc}")
        print("Install: pip install async-rithmic")
        return 1
    except Exception as exc:
        print(f"CONNECTION FAILED: {exc}")
        return 1

    print(f"connected: {client.connected}")
    print(f"requested symbol: {args.symbol}")
    print(f"requested exchange: {args.exchange}")
    print(f"requested start: {start_utc.isoformat()}")
    print(f"requested end:   {end_utc.isoformat()}")
    print(f"data type: {args.data_type}")
    print(f"max_pages: {args.max_pages}")

    # probe-only: list valid system names and exit cleanly (no symbol required)
    if args.probe_only:
        try:
            names = await client.list_system_names()
        except Exception as exc:
            print(f"PROBE FAILED: {exc}")
            await client.disconnect()
            return 1
        await client.disconnect()
        print(f"valid system_name values: {names}")
        if creds.get("system_name") in names:
            print(f"configured system_name '{creds['system_name']}' is VALID")
            return 0
        print(f"configured system_name '{creds.get('system_name')}' NOT in list — update RITHMIC_SYSTEM_NAME")
        return 1

    # data download — symbol required from here on
    if not args.symbol:
        print("MISSING SYMBOL: pass --symbol or set RITHMIC_SYMBOL")
        await client.disconnect()
        return 1

    # download — async_rithmic handles pagination internally via max_pages
    error: str | None = None
    try:
        if args.data_type == "bars":
            records = await client.get_historical_bars(
                args.symbol,
                args.exchange,
                start_utc,
                end_utc,
                bar_type=args.bar_type,
                bar_type_periods=args.bar_size,
                max_pages=args.max_pages,
            )
        else:
            records = await client.get_historical_ticks(
                args.symbol,
                args.exchange,
                start_utc,
                end_utc,
                max_pages=args.max_pages,
            )
    except Exception as exc:
        error = str(exc)
        records = []
    finally:
        await client.disconnect()

    # results
    table = _records_to_table(records)
    data_label = _infer_data_label(table.schema)
    first_ts = _extract_first_timestamp(records)
    last_ts = _extract_last_timestamp(records)
    columns = [f.name for f in table.schema]

    print(f"number of {data_label} returned: {len(records)}")
    print(f"first timestamp: {first_ts.isoformat() if first_ts else 'N/A'}")
    print(f"last timestamp:  {last_ts.isoformat() if last_ts else 'N/A'}")
    print(f"returned fields: {columns}")
    print(f"inferred data label: {data_label}")

    if error:
        print(f"error: {error}")

    # save
    out_dir = _output_dir(_REPO, args.symbol, start_utc, base=args.output_base)
    parquet_path = None
    if table.num_rows > 0:
        parquet_path = _save_parquet(table, out_dir)
        print(f"parquet: {parquet_path}")

    manifest_path = _write_manifest(
        out_dir,
        source="rithmic",
        symbol=args.symbol,
        exchange=args.exchange,
        request_start=start_utc.isoformat(),
        request_end=end_utc.isoformat(),
        data_label=data_label,
        row_count=table.num_rows,
        first_timestamp=first_ts.isoformat() if first_ts else None,
        last_timestamp=last_ts.isoformat() if last_ts else None,
        columns=columns,
        api_env=creds.get("system_name", ""),
        error=error,
    )
    print(f"manifest: {manifest_path}")

    if error:
        print(f"\nFAILED: {error}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    if is_windows():
        print(
            "REFUSED: rithmic_download_test.py must run on CHI404 bare metal, "
            "not the dev workstation (BLUEPRINT §4).",
            file=sys.stderr,
        )
        return 1
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
