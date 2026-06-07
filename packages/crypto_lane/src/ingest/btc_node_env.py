"""Load btc-node tunnel endpoints from .btc-node.env."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.types import repo_root_from_lane


class NodeEnvError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeEnv:
    btc_rpc_url: str
    btc_rpc_user: str
    btc_rpc_pass: str
    btc_zmq_rawblock: str
    btc_zmq_rawtx: str

    @classmethod
    def load(cls, path: Path | None = None) -> NodeEnv:
        ensure_crypto_env()
        if path is None:
            root = repo_root_from_lane()
            import sys

            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            import desk_env

            p = desk_env.resolve_btc_node_env_path(root) or (root / ".btc-node.env")
        else:
            p = path

        if not p.is_file():
            raise NodeEnvError(
                f".btc-node.env missing at {p}. Copy .btc-node.env.example after ssh -fN btc-node"
            )

        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(p).st_mode)
            if mode & 0o077:
                raise NodeEnvError(f"{p} mode {oct(mode)} too permissive — chmod 0600 expected")

        kv: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()

        required = ("BTC_RPC_URL", "BTC_RPC_USER", "BTC_RPC_PASS", "BTC_ZMQ_RAWBLOCK", "BTC_ZMQ_RAWTX")
        missing = [k for k in required if k not in kv]
        if missing:
            raise NodeEnvError(f"missing BTC keys in {p}: {missing}")

        return cls(
            btc_rpc_url=kv["BTC_RPC_URL"],
            btc_rpc_user=kv["BTC_RPC_USER"],
            btc_rpc_pass=kv["BTC_RPC_PASS"],
            btc_zmq_rawblock=kv["BTC_ZMQ_RAWBLOCK"],
            btc_zmq_rawtx=kv["BTC_ZMQ_RAWTX"],
        )
