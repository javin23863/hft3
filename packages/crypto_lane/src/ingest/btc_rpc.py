"""Bitcoin Core JSON-RPC client (stdlib only)."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from crypto_lane.src.ingest.btc_node_env import NodeEnv


class BtcRpcError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChainInfo:
    chain: str
    blocks: int
    headers: int
    best_block_hash: str
    difficulty: float
    verification_progress: float
    initial_block_download: bool
    size_on_disk: int
    pruned: bool


@dataclass(frozen=True)
class MempoolInfo:
    size: int
    bytes: int
    usage: int
    total_fee: float
    min_fee: float
    mempool_min_fee: float
    relay_fee: float


@dataclass(frozen=True)
class NetworkInfo:
    version: int
    subversion: str
    connections: int
    connections_in: int
    connections_out: int


class BtcRpc:
    def __init__(self, env: NodeEnv | None = None, *, timeout: float = 10.0) -> None:
        self._env = env or NodeEnv.load()
        self._timeout = timeout
        parsed = urlparse(self._env.btc_rpc_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8332
        self._url = f"{parsed.scheme or 'http'}://{host}:{port}/"
        user = parsed.username or self._env.btc_rpc_user
        password = parsed.password or self._env.btc_rpc_pass
        self._auth = base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, method: str, *params: Any) -> Any:
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": "hft3", "method": method, "params": list(params)}
        ).encode()
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={
                "Content-Type": "text/plain",
                "Authorization": f"Basic {self._auth}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise BtcRpcError(f"BTC RPC HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise BtcRpcError(f"BTC RPC URL error: {exc.reason}") from exc
        if body.get("error"):
            raise BtcRpcError(f"BTC RPC error: {body['error']}")
        return body["result"]

    def getblockchaininfo(self) -> ChainInfo:
        r = self.call("getblockchaininfo")
        return ChainInfo(
            chain=r["chain"],
            blocks=int(r["blocks"]),
            headers=int(r["headers"]),
            best_block_hash=r["bestblockhash"],
            difficulty=float(r["difficulty"]),
            verification_progress=float(r["verificationprogress"]),
            initial_block_download=bool(r.get("initialblockdownload", False)),
            size_on_disk=int(r["size_on_disk"]),
            pruned=bool(r.get("pruned", False)),
        )

    def getmempoolinfo(self) -> MempoolInfo:
        r = self.call("getmempoolinfo")
        return MempoolInfo(
            size=int(r["size"]),
            bytes=int(r["bytes"]),
            usage=int(r["usage"]),
            total_fee=float(r.get("total_fee", 0.0)),
            min_fee=float(r.get("minrelaytxfee", 0.0)),
            mempool_min_fee=float(r.get("mempoolminfee", 0.0)),
            relay_fee=float(r.get("minrelaytxfee", 0.0)),
        )

    def getrawmempool(self, verbose: bool = False) -> list[str] | dict[str, Any]:
        return self.call("getrawmempool", verbose)

    def getnetworkinfo(self) -> NetworkInfo:
        r = self.call("getnetworkinfo")
        return NetworkInfo(
            version=int(r["version"]),
            subversion=r["subversion"],
            connections=int(r["connections"]),
            connections_in=int(r.get("connections_in", 0)),
            connections_out=int(r.get("connections_out", 0)),
        )

    def estimatesmartfee(self, target_blocks: int = 6) -> float:
        r = self.call("estimatesmartfee", target_blocks)
        return float(r.get("feerate", 0.0))
