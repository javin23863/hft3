from __future__ import annotations

import ctypes
import os
from ctypes import (
    CDLL,
    POINTER,
    RTLD_LOCAL,
    Structure,
    c_char_p,
    c_double,
    c_int,
    c_int32,
    c_uint64,
    c_void_p,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


class RithmicApiError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"rithmic_api error {code}: {message}")


class RithmicApiLibraryNotFoundError(FileNotFoundError):
    pass


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(20):
        if (cur / ".git").exists() or (cur / "pyproject.toml").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return Path(__file__).resolve().parents[5]


_BUILD_HINT = (
    "Build the C++ gateway: "
    "Linux/CHI404: `cmake -S . -B build -G 'Unix Makefiles' && cmake --build build`; "
    "Windows/MSVC: `cmake -S . -B build-msvc -G Ninja -DCMAKE_BUILD_TYPE=Release "
    "&& cmake --build build-msvc --target rithmic_gateway_shared`"
)


def _candidate_so_paths() -> list[Path]:
    repo = _repo_root()
    env = os.environ.get("HFT3_RITHMIC_GATEWAY_SO")
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
        return paths
    if os.name == "nt":
        paths.append(repo / "build-msvc" / "rithmic_gateway" / "rithmic_gateway_shared.dll")
        paths.append(repo / "build" / "rithmic_gateway" / "rithmic_gateway_shared.dll")
    else:
        paths.append(repo / "rithmic_gateway_shared.so")
        paths.append(repo / "build" / "rithmic_gateway" / "librithmic_gateway_shared.so")
    return paths


def _load_library() -> CDLL:
    tried: list[Path] = []
    for p in _candidate_so_paths():
        if p.exists():
            return CDLL(str(p), mode=RTLD_LOCAL)
        tried.append(p)
    raise RithmicApiLibraryNotFoundError(
        "Rithmic gateway shared library not found. Tried: "
        + ", ".join(str(p) for p in tried)
        + ". " + _BUILD_HINT
    )


@dataclass
class MarketDataEvent:
    timestamp_ns: int = 0
    order_id: int = 0
    action: str = ""
    side: str = ""
    price: float = 0.0
    size: int = 0

    @classmethod
    def from_c(cls, ev: "CMarketDataEvent") -> "MarketDataEvent":
        def _decode_char(value) -> str:
            if not value:
                return ""
            if isinstance(value, int):
                return chr(value)
            if isinstance(value, (bytes, bytearray)):
                return value.decode("utf-8", errors="replace") if value else ""
            return str(value)

        return cls(
            timestamp_ns=int(ev.timestamp_ns),
            order_id=int(ev.order_id),
            action=_decode_char(ev.action),
            side=_decode_char(ev.side),
            price=float(ev.price),
            size=int(ev.size),
        )

    def to_dict(self) -> dict:
        return {
            "timestamp_ns": self.timestamp_ns,
            "order_id": self.order_id,
            "action": self.action,
            "side": self.side,
            "price": self.price,
            "size": self.size,
        }


class CMarketDataEvent(Structure):
    _fields_ = [
        ("timestamp_ns", c_uint64),
        ("order_id", c_uint64),
        ("action", ctypes.c_char),
        ("side", ctypes.c_char),
        ("price", c_double),
        ("size", c_int32),
    ]


@dataclass
class OrderEvent:
    timestamp_ns: int = 0
    callback_monotonic_ns: int = 0
    callback_wall_ns: int = 0
    order_id: int = 0
    event_type: str = ""
    side: str = ""
    order_type: str = ""
    price: float = 0.0
    size: int = 0
    filled_size: int = 0
    total_filled: int = 0
    total_unfilled: int = 0
    user_msg: str = ""
    tag: str = ""

    @classmethod
    def from_c(cls, ev: "COrderEvent") -> "OrderEvent":
        def _decode_char(value) -> str:
            if not value:
                return ""
            if isinstance(value, int):
                return chr(value)
            if isinstance(value, (bytes, bytearray)):
                return value.decode("utf-8", errors="replace") if value else ""
            return str(value)

        def _decode_string(value) -> str:
            if not value:
                return ""
            if isinstance(value, (bytes, bytearray)):
                return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")
            return str(value)

        return cls(
            timestamp_ns=int(ev.timestamp_ns),
            callback_monotonic_ns=int(ev.callback_monotonic_ns),
            callback_wall_ns=int(ev.callback_wall_ns),
            order_id=int(ev.order_id),
            event_type=_decode_char(ev.event_type),
            side=_decode_char(ev.side),
            order_type=_decode_char(ev.order_type),
            price=float(ev.price),
            size=int(ev.size),
            filled_size=int(ev.filled_size),
            total_filled=int(ev.total_filled),
            total_unfilled=int(ev.total_unfilled),
            user_msg=_decode_string(ev.user_msg),
            tag=_decode_string(ev.tag),
        )

    def to_dict(self) -> dict:
        return {
            "timestamp_ns": self.timestamp_ns,
            "callback_monotonic_ns": self.callback_monotonic_ns,
            "callback_wall_ns": self.callback_wall_ns,
            "order_id": self.order_id,
            "event_type": self.event_type,
            "side": self.side,
            "order_type": self.order_type,
            "price": self.price,
            "size": self.size,
            "filled_size": self.filled_size,
            "total_filled": self.total_filled,
            "total_unfilled": self.total_unfilled,
            "user_msg": self.user_msg,
            "tag": self.tag,
        }


class COrderEvent(Structure):
    _fields_ = [
        ("timestamp_ns", c_uint64),
        ("callback_monotonic_ns", c_uint64),
        ("callback_wall_ns", c_uint64),
        ("order_id", c_uint64),
        ("event_type", ctypes.c_char),
        ("side", ctypes.c_char),
        ("order_type", ctypes.c_char),
        ("price", c_double),
        ("size", c_int32),
        ("filled_size", c_int32),
        ("total_filled", c_int32),
        ("total_unfilled", c_int32),
        ("user_msg", ctypes.c_char * 64),
        ("tag", ctypes.c_char * 64),
    ]


class CConnectionConfig(Structure):
    pass


CConnectionConfig._fields_ = [
    ("environment", c_char_p),
    ("username", c_char_p),
    ("password", c_char_p),
    ("app_name", c_char_p),
    ("app_version", c_char_p),
    ("ssl_cert_path", c_char_p),
    ("log_file_path", c_char_p),
    ("md_connect_point", c_char_p),
    ("ts_connect_point", c_char_p),
    ("rep_connect_point", c_char_p),
    ("pnl_connect_point", c_char_p),
    ("ih_connect_point", c_char_p),
    ("env_vars", POINTER(c_char_p)),
    ("env_vars_count", c_int),
]


@dataclass
class ConnectionConfig:
    environment: str = ""
    username: str = ""
    password: str = ""
    app_name: str = "HFT3"
    app_version: str = "1.0"
    ssl_cert_path: str = ""
    log_file_path: str = ""
    md_connect_point: str = ""
    ts_connect_point: str = ""
    rep_connect_point: str = ""
    pnl_connect_point: str = ""
    ih_connect_point: str = ""
    env_vars: list[str] = field(default_factory=list)

    def to_c(self) -> CConnectionConfig:
        refs: list[bytes] = []
        c_strings: list[c_char_p] = []
        for s in (
            self.environment,
            self.username,
            self.password,
            self.app_name,
            self.app_version,
            self.ssl_cert_path,
            self.log_file_path,
            self.md_connect_point,
            self.ts_connect_point,
            self.rep_connect_point,
            self.pnl_connect_point,
            self.ih_connect_point,
        ):
            if s is None:
                c_strings.append(c_char_p(None))
                continue
            b = s.encode("utf-8") if isinstance(s, str) else s
            refs.append(b)
            c_strings.append(c_char_p(b))

        env_b_list: list[bytes] = []
        env_cstrings: list[c_char_p] = []
        for s in self.env_vars:
            b = s.encode("utf-8") if isinstance(s, str) else s
            env_b_list.append(b)
            env_cstrings.append(c_char_p(b))

        n = len(env_cstrings)
        ArrayType = c_char_p * (n + 1)
        env_array = ArrayType()
        for i, cs in enumerate(env_cstrings):
            env_array[i] = cs
        env_array[n] = c_char_p(None)

        cfg = CConnectionConfig(
            environment=c_strings[0],
            username=c_strings[1],
            password=c_strings[2],
            app_name=c_strings[3],
            app_version=c_strings[4],
            ssl_cert_path=c_strings[5],
            log_file_path=c_strings[6],
            md_connect_point=c_strings[7],
            ts_connect_point=c_strings[8],
            rep_connect_point=c_strings[9],
            pnl_connect_point=c_strings[10],
            ih_connect_point=c_strings[11],
            env_vars=ctypes.cast(env_array, POINTER(c_char_p)),
            env_vars_count=n,
        )
        cfg._refs = refs
        cfg._env_array = env_array
        cfg._env_cstrings = env_cstrings
        cfg._env_b_list = env_b_list
        return cfg


class RithmicApiBridge:
    """ctypes wrapper around ``librithmic_gateway_shared.so``.

    Not thread-safe: one polling thread per handle. R|API+ internal callbacks
    deliver events asynchronously into the SPSC queue; Python consumes them via
    :meth:`try_pop_event` from a single thread.
    """

    def __init__(self, lib: CDLL) -> None:
        self._lib = lib
        self._configure()
        self._handle: c_void_p | None = None

    @classmethod
    def load(cls) -> "RithmicApiBridge":
        lib = _load_library()
        return cls(lib)

    def _configure(self) -> None:
        L = self._lib
        L.hft_rithmic_adapter_create.argtypes = [POINTER(CConnectionConfig)]
        L.hft_rithmic_adapter_create.restype = c_void_p

        L.hft_rithmic_adapter_initialize.argtypes = [c_void_p]
        L.hft_rithmic_adapter_initialize.restype = c_int

        L.hft_rithmic_adapter_connect.argtypes = [c_void_p]
        L.hft_rithmic_adapter_connect.restype = c_int

        L.hft_rithmic_adapter_disconnect.argtypes = [c_void_p]
        L.hft_rithmic_adapter_disconnect.restype = None

        L.hft_rithmic_adapter_destroy.argtypes = [c_void_p]
        L.hft_rithmic_adapter_destroy.restype = None

        L.hft_rithmic_adapter_subscribe_mbo.argtypes = [c_void_p, c_char_p, c_char_p]
        L.hft_rithmic_adapter_subscribe_mbo.restype = c_int

        L.hft_rithmic_adapter_send_order.argtypes = [
            c_void_p,
            c_char_p,
            ctypes.c_char,
            c_int32,
            c_double,
        ]
        L.hft_rithmic_adapter_send_order.restype = c_int

        self._send_order_with_user_msg = None
        try:
            send_with_msg = L.hft_rithmic_adapter_send_order_with_user_msg
        except AttributeError:
            send_with_msg = None
        if send_with_msg is not None:
            send_with_msg.argtypes = [
                c_void_p,
                c_char_p,
                ctypes.c_char,
                c_int32,
                c_double,
                c_char_p,
            ]
            send_with_msg.restype = c_int
            self._send_order_with_user_msg = send_with_msg

        L.hft_rithmic_adapter_cancel_order.argtypes = [c_void_p, c_char_p]
        L.hft_rithmic_adapter_cancel_order.restype = c_int

        L.hft_rithmic_adapter_try_pop_event.argtypes = [c_void_p, POINTER(CMarketDataEvent)]
        L.hft_rithmic_adapter_try_pop_event.restype = c_int

        L.hft_rithmic_adapter_try_pop_order_event.argtypes = [c_void_p, POINTER(COrderEvent)]
        L.hft_rithmic_adapter_try_pop_order_event.restype = c_int

        L.hft_rithmic_adapter_last_error.argtypes = [c_void_p]
        L.hft_rithmic_adapter_last_error.restype = c_char_p

        L.hft_rithmic_adapter_get_env_key.argtypes = [c_void_p]
        L.hft_rithmic_adapter_get_env_key.restype = c_char_p

        L.hft_rithmic_adapter_get_account_id.argtypes = [c_void_p]
        L.hft_rithmic_adapter_get_account_id.restype = c_char_p

        L.hft_rithmic_adapter_get_trade_route.argtypes = [c_void_p]
        L.hft_rithmic_adapter_get_trade_route.restype = c_char_p

        L.hft_rithmic_adapter_is_connected.argtypes = [c_void_p]
        L.hft_rithmic_adapter_is_connected.restype = c_int

    def _raise_if_nonzero(self, rc: int) -> None:
        if rc != 0:
            raise RithmicApiError(rc, self.last_error())

    def _require_handle(self) -> c_void_p:
        if self._handle is None:
            raise RithmicApiError(1, "no active adapter handle")
        return self._handle

    def create(self, cfg: ConnectionConfig) -> "RithmicApiBridge":
        c_cfg = cfg.to_c()
        handle = self._lib.hft_rithmic_adapter_create(ctypes.byref(c_cfg))
        if not handle:
            raise RithmicApiError(
                2, f"hft_rithmic_adapter_create returned null: {self.last_error()}"
            )
        self._handle = c_void_p(handle)
        return self

    def initialize(self) -> "RithmicApiBridge":
        h = self._require_handle()
        self._raise_if_nonzero(self._lib.hft_rithmic_adapter_initialize(h))
        return self

    def connect(self) -> "RithmicApiBridge":
        h = self._require_handle()
        self._raise_if_nonzero(self._lib.hft_rithmic_adapter_connect(h))
        return self

    def disconnect(self) -> None:
        h = self._handle
        if h is not None:
            self._lib.hft_rithmic_adapter_disconnect(h)

    def destroy(self) -> None:
        h = self._handle
        if h is not None:
            self._lib.hft_rithmic_adapter_destroy(h)
            self._handle = None

    def subscribe_mbo(self, symbol: str, exchange: str = "CME") -> None:
        h = self._require_handle()
        rc = self._lib.hft_rithmic_adapter_subscribe_mbo(
            h, symbol.encode("utf-8"), exchange.encode("utf-8")
        )
        self._raise_if_nonzero(rc)

    def send_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        user_msg: str | None = None,
    ) -> None:
        h = self._require_handle()
        if not isinstance(side, str) or len(side) != 1:
            raise ValueError(f"side must be a single character; got {side!r}")
        if user_msg and self._send_order_with_user_msg is not None:
            rc = self._send_order_with_user_msg(
                h,
                symbol.encode("utf-8"),
                ctypes.c_char(side.encode("utf-8")),
                c_int32(int(qty)),
                c_double(float(price)),
                user_msg.encode("utf-8"),
            )
        else:
            rc = self._lib.hft_rithmic_adapter_send_order(
                h,
                symbol.encode("utf-8"),
                ctypes.c_char(side.encode("utf-8")),
                c_int32(int(qty)),
                c_double(float(price)),
            )
        self._raise_if_nonzero(rc)

    def cancel_order(self, order_id: str) -> None:
        h = self._require_handle()
        rc = self._lib.hft_rithmic_adapter_cancel_order(
            h, order_id.encode("utf-8")
        )
        self._raise_if_nonzero(rc)

    def try_pop_event(self) -> Optional[MarketDataEvent]:
        h = self._require_handle()
        ev = CMarketDataEvent()
        rc = self._lib.hft_rithmic_adapter_try_pop_event(h, ctypes.byref(ev))
        if rc == 2:
            return None
        if rc == 0:
            return MarketDataEvent.from_c(ev)
        raise RithmicApiError(rc, self.last_error())

    def try_pop_order_event(self) -> Optional[OrderEvent]:
        h = self._require_handle()
        ev = COrderEvent()
        rc = self._lib.hft_rithmic_adapter_try_pop_order_event(h, ctypes.byref(ev))
        if rc == 2:
            return None
        if rc == 0:
            return OrderEvent.from_c(ev)
        raise RithmicApiError(rc, self.last_error())

    def last_error(self) -> str:
        h = self._handle
        if h is None:
            return "no active adapter handle"
        msg = self._lib.hft_rithmic_adapter_last_error(h)
        if not msg:
            return ""
        try:
            return msg.decode("utf-8")
        except Exception:
            return str(msg)

    def env_key(self) -> str:
        h = self._require_handle()
        msg = self._lib.hft_rithmic_adapter_get_env_key(h)
        return msg.decode("utf-8") if msg else ""

    def account_id(self) -> str:
        h = self._require_handle()
        msg = self._lib.hft_rithmic_adapter_get_account_id(h)
        return msg.decode("utf-8") if msg else ""

    def trade_route(self) -> str:
        h = self._require_handle()
        msg = self._lib.hft_rithmic_adapter_get_trade_route(h)
        return msg.decode("utf-8") if msg else ""

    def is_connected(self) -> bool:
        if self._handle is None:
            return False
        return bool(self._lib.hft_rithmic_adapter_is_connected(self._handle))


def locate_library() -> Path | None:
    for p in _candidate_so_paths():
        if p.exists():
            return p
    return None
