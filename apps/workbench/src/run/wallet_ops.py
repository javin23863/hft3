"""Bitcoin Core wallet operations for the Workbench operator surface."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


BTC_NODE_HOST = "btc-node"
BITCOIN_DATADIR = "/var/lib/bitcoin/.bitcoin"
OPS_WALLET = "qx_ops_hot_wallet"
WATCH_ONLY_WALLET = "qx_research_watch_only"
MIN_SEND_BTC = Decimal("0.00000001")
DEFAULT_CONFIRMATION_TARGET = 6
FEE_ESTIMATE_MODES = {"economical", "conservative", "unset"}


class WalletError(RuntimeError):
    """Raised for wallet command failures safe to show to an operator."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def _run_ssh(remote_command: str, *, stdin: str | None = None, timeout: int = 45) -> CommandResult:
    proc = subprocess.run(
        ["ssh", "-4", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", BTC_NODE_HOST, remote_command],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "wallet command failed").strip()
        raise WalletError(message)
    return CommandResult(stdout=proc.stdout.strip(), stderr=proc.stderr.strip())


def _bitcoin_cli(args: list[str], *, wallet: str | None = None, timeout: int = 45) -> str:
    base = [
        "sudo",
        "-n",
        "-u",
        "bitcoin",
        "bitcoin-cli",
        f"-datadir={BITCOIN_DATADIR}",
    ]
    if wallet:
        base.append(f"-rpcwallet={wallet}")
    command = " ".join(shlex.quote(part) for part in [*base, *args])
    return _run_ssh(command, timeout=timeout).stdout


def _json_cli(args: list[str], *, wallet: str | None = None, timeout: int = 45) -> dict[str, Any]:
    text = _bitcoin_cli(args, wallet=wallet, timeout=timeout)
    return json.loads(text) if text else {}


def _json_list_cli(args: list[str], *, wallet: str | None = None, timeout: int = 45) -> list[Any]:
    text = _bitcoin_cli(args, wallet=wallet, timeout=timeout)
    return json.loads(text) if text else []


def _remote_python_json(script: str, *, stdin: str | None = None, timeout: int = 45) -> dict[str, Any]:
    command = "python3 - <<'PY'\n" + script.strip() + "\nPY"
    text = _run_ssh("bash -lc " + shlex.quote(command), stdin=stdin, timeout=timeout).stdout
    return json.loads(text) if text else {}


def wallet_snapshot() -> dict[str, Any]:
    """Return a read-only view of node and wallet state."""
    script = f"""
import json
import subprocess
from decimal import Decimal

DATADIR = {json.dumps(BITCOIN_DATADIR)}
OPS_WALLET = {json.dumps(OPS_WALLET)}
WATCH_ONLY_WALLET = {json.dumps(WATCH_ONLY_WALLET)}


def cli(args, wallet=None):
    cmd = ["sudo", "-n", "-u", "bitcoin", "bitcoin-cli", "-datadir=" + DATADIR]
    if wallet:
        cmd.append("-rpcwallet=" + wallet)
    cmd.extend(args)
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()


def obj(args, wallet=None):
    text = cli(args, wallet=wallet)
    return json.loads(text) if text else {{}}


blockchain = obj(["getblockchaininfo"])
network = obj(["getnetworkinfo"])
mempool = obj(["getmempoolinfo"])
hot_info = obj(["getwalletinfo"], wallet=OPS_WALLET)
watch_info = obj(["getwalletinfo"], wallet=WATCH_ONLY_WALLET)
hot_balances = obj(["getbalances"], wallet=OPS_WALLET)
watch_balances = obj(["getbalances"], wallet=WATCH_ONLY_WALLET)

print(json.dumps({{
    "node": {{
        "chain": blockchain.get("chain"),
        "blocks": blockchain.get("blocks"),
        "headers": blockchain.get("headers"),
        "initialblockdownload": blockchain.get("initialblockdownload"),
        "verificationprogress": blockchain.get("verificationprogress"),
        "networkactive": network.get("networkactive"),
        "connections": network.get("connections"),
        "mempool_loaded": mempool.get("loaded"),
        "mempool_size": mempool.get("size"),
    }},
    "hot_wallet": {{
        "walletname": hot_info.get("walletname"),
        "private_keys_enabled": hot_info.get("private_keys_enabled"),
        "descriptors": hot_info.get("descriptors"),
        "avoid_reuse": hot_info.get("avoid_reuse"),
        "unlocked_until": hot_info.get("unlocked_until"),
        "balances": hot_balances,
    }},
    "watch_only_wallet": {{
        "walletname": watch_info.get("walletname"),
        "private_keys_enabled": watch_info.get("private_keys_enabled"),
        "descriptors": watch_info.get("descriptors"),
        "avoid_reuse": watch_info.get("avoid_reuse"),
        "balances": watch_balances,
    }},
}}))
"""
    return _remote_python_json(script, timeout=45)


def create_receive_address(label: str, *, wallet: str = OPS_WALLET) -> dict[str, Any]:
    clean_label = (label or "ops-test").strip()[:80]
    script = f"""
import json
import subprocess
from decimal import Decimal

DATADIR = {json.dumps(BITCOIN_DATADIR)}
WALLET = {json.dumps(wallet)}
LABEL = {json.dumps(clean_label)}


def cli(args, wallet=None):
    cmd = ["sudo", "-n", "-u", "bitcoin", "bitcoin-cli", "-datadir=" + DATADIR]
    if wallet:
        cmd.append("-rpcwallet=" + wallet)
    cmd.extend(args)
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()


def obj(args, wallet=None):
    text = cli(args, wallet=wallet)
    return json.loads(text) if text else {{}}


address = cli(["getnewaddress", LABEL, "bech32"], wallet=WALLET)
validation = obj(["validateaddress", address])
address_info = obj(["getaddressinfo", address], wallet=WALLET)
print(json.dumps({{
    "address": address,
    "label": LABEL,
    "isvalid": validation.get("isvalid"),
    "ismine": address_info.get("ismine"),
    "iswatchonly": address_info.get("iswatchonly"),
    "iswitness": address_info.get("iswitness"),
}}))
"""
    return _remote_python_json(script, timeout=45)


def _estimate_options(
    *,
    subtract_fee_from_amount: bool,
    conf_target: int,
    estimate_mode: str,
    fee_rate_sat_vb: str | None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "replaceable": True,
        "conf_target": conf_target,
        "estimate_mode": estimate_mode,
        "includeWatching": False,
        "lockUnspents": False,
    }
    if subtract_fee_from_amount:
        options["subtractFeeFromOutputs"] = [0]
    if fee_rate_sat_vb:
        options["fee_rate"] = str(_parse_fee_rate(fee_rate_sat_vb))
    return options


def recent_transactions(count: int = 10) -> list[dict[str, Any]]:
    rows = _json_list_cli(["listtransactions", "*", str(count), "0", "true"], wallet=OPS_WALLET)
    return [row for row in rows if isinstance(row, dict)]


def validate_destination(address: str) -> dict[str, Any]:
    clean = address.strip()
    if not clean:
        return {"isvalid": False, "address": ""}
    return _json_cli(["validateaddress", clean])


def _parse_amount_btc(amount: str) -> Decimal:
    try:
        parsed = Decimal(amount.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise WalletError("Amount must be a valid BTC decimal.") from exc
    if parsed < MIN_SEND_BTC:
        raise WalletError("Amount must be at least 1 satoshi.")
    return parsed


def _parse_conf_target(conf_target: int | str) -> int:
    try:
        parsed = int(conf_target)
    except (TypeError, ValueError) as exc:
        raise WalletError("Confirmation target must be a whole number of blocks.") from exc
    if parsed < 1 or parsed > 1008:
        raise WalletError("Confirmation target must be between 1 and 1008 blocks.")
    return parsed


def _parse_estimate_mode(estimate_mode: str) -> str:
    parsed = (estimate_mode or "economical").strip().lower()
    if parsed not in FEE_ESTIMATE_MODES:
        raise WalletError("Fee estimate mode must be economical, conservative, or unset.")
    return parsed


def _parse_fee_rate(fee_rate_sat_vb: str | None) -> Decimal | None:
    if fee_rate_sat_vb is None or not str(fee_rate_sat_vb).strip():
        return None
    try:
        parsed = Decimal(str(fee_rate_sat_vb).strip())
    except InvalidOperation as exc:
        raise WalletError("Fee rate must be a valid sat/vB decimal.") from exc
    if parsed <= 0:
        raise WalletError("Fee rate must be greater than zero.")
    return parsed


def preview_send(
    destination: str,
    amount_btc: str,
    *,
    subtract_fee_from_amount: bool = False,
    conf_target: int | str = DEFAULT_CONFIRMATION_TARGET,
    estimate_mode: str = "economical",
    fee_rate_sat_vb: str | None = None,
) -> dict[str, Any]:
    """Return a funded-PSBT preview without signing or broadcasting."""
    clean_destination = destination.strip()
    amount = _parse_amount_btc(amount_btc)
    blocks = _parse_conf_target(conf_target)
    mode = _parse_estimate_mode(estimate_mode)
    fee_rate = _parse_fee_rate(fee_rate_sat_vb)
    options = _estimate_options(
        subtract_fee_from_amount=subtract_fee_from_amount,
        conf_target=blocks,
        estimate_mode=mode,
        fee_rate_sat_vb=str(fee_rate) if fee_rate is not None else None,
    )
    script = f"""
import json
import subprocess
from decimal import Decimal

DATADIR = {json.dumps(BITCOIN_DATADIR)}
WALLET = {json.dumps(OPS_WALLET)}
DEST = {json.dumps(clean_destination)}
AMOUNT = {json.dumps(format(amount, "f"))}
OPTIONS = {options!r}
CONF_TARGET = {json.dumps(blocks)}
ESTIMATE_MODE = {json.dumps(mode)}
SUBTRACT_FEE = {bool(subtract_fee_from_amount)!r}


def run(args, wallet=None):
    cmd = ["sudo", "-n", "-u", "bitcoin", "bitcoin-cli", "-datadir=" + DATADIR]
    if wallet:
        cmd.append("-rpcwallet=" + wallet)
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def cli(args, wallet=None):
    proc = run(args, wallet=wallet)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def obj(args, wallet=None):
    text = cli(args, wallet=wallet)
    return json.loads(text) if text else {{}}


validation = obj(["validateaddress", DEST])
balances = obj(["getbalances"], wallet=WALLET)
trusted = balances.get("mine", {{}}).get("trusted", 0)
fee_estimate = {{}}
try:
    fee_estimate = obj(["estimatesmartfee", str(CONF_TARGET), ESTIMATE_MODE])
except Exception as exc:
    fee_estimate = {{"error": str(exc)}}

result = {{
    "destination": DEST,
    "amount_btc": AMOUNT,
    "subtract_fee_from_amount": SUBTRACT_FEE,
    "conf_target": CONF_TARGET,
    "estimate_mode": ESTIMATE_MODE,
    "fee_rate_sat_vb": OPTIONS.get("fee_rate"),
    "address_valid": validation.get("isvalid", False),
    "spendable_btc": trusted,
    "fee_estimate": fee_estimate,
    "can_fund": False,
}}
if not result["address_valid"]:
    result["error"] = "Destination address is not valid."
    print(json.dumps(result))
    raise SystemExit(0)

outputs = json.dumps([{{DEST: AMOUNT}}])
psbt = run(["walletcreatefundedpsbt", "[]", outputs, "0", json.dumps(OPTIONS), "false"], wallet=WALLET)
if psbt.returncode != 0:
    result["error"] = (psbt.stderr or psbt.stdout).strip()
else:
    funded = json.loads(psbt.stdout)
    fee = funded.get("fee", 0)
    result.update({{
        "can_fund": True,
        "network_fee_btc": fee,
        "change_position": funded.get("changepos"),
        "total_debit_btc": AMOUNT if SUBTRACT_FEE else format(Decimal(AMOUNT) + Decimal(str(fee)), ".8f"),
    }})
print(json.dumps(result))
"""
    return _remote_python_json(script, timeout=60)


def send_btc(
    destination: str,
    amount_btc: str,
    passphrase: str,
    *,
    subtract_fee_from_amount: bool = False,
    conf_target: int | str = DEFAULT_CONFIRMATION_TARGET,
    estimate_mode: str = "economical",
    fee_rate_sat_vb: str | None = None,
) -> dict[str, Any]:
    """Unlock briefly, send one transaction, then lock.

    The passphrase is supplied over stdin to the remote shell and never appears
    in the local or remote command line.
    """
    clean_destination = destination.strip()
    amount = _parse_amount_btc(amount_btc)
    blocks = _parse_conf_target(conf_target)
    mode = _parse_estimate_mode(estimate_mode)
    fee_rate = _parse_fee_rate(fee_rate_sat_vb)
    if not passphrase:
        raise WalletError("Wallet passphrase is required.")

    script = f"""
set -euo pipefail
DATADIR={shlex.quote(BITCOIN_DATADIR)}
WALLET={shlex.quote(OPS_WALLET)}
DEST={shlex.quote(clean_destination)}
AMOUNT={shlex.quote(format(amount, "f"))}
SUBTRACT_FEE={shlex.quote("true" if subtract_fee_from_amount else "false")}
CONF_TARGET={shlex.quote(str(blocks))}
ESTIMATE_MODE={shlex.quote(mode)}
FEE_RATE={shlex.quote(str(fee_rate) if fee_rate is not None else "")}
IFS= read -r pass
VALID=$(sudo -n -u bitcoin bitcoin-cli -datadir="$DATADIR" validateaddress "$DEST" | python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("isvalid") else "0")')
if [ "$VALID" != "1" ]; then
  echo "Destination address is not a valid Bitcoin address." >&2
  exit 2
fi
trap 'sudo -n -u bitcoin bitcoin-cli -datadir="$DATADIR" -rpcwallet="$WALLET" walletlock >/dev/null 2>&1 || true' EXIT
printf '%s\\n' "$pass" | sudo -n -u bitcoin bitcoin-cli -datadir="$DATADIR" -rpcwallet="$WALLET" -stdinwalletpassphrase walletpassphrase 60 >/dev/null
unset pass
cmd=(
  sudo -n -u bitcoin bitcoin-cli -datadir="$DATADIR" -rpcwallet="$WALLET" -named sendtoaddress
  address="$DEST"
  amount="$AMOUNT"
  subtractfeefromamount="$SUBTRACT_FEE"
  replaceable=true
  conf_target="$CONF_TARGET"
  estimate_mode="$ESTIMATE_MODE"
  avoid_reuse=true
  verbose=true
)
if [ -n "$FEE_RATE" ]; then
  cmd+=(fee_rate="$FEE_RATE")
fi
"${{cmd[@]}}"
""".strip()
    stdout = _run_ssh("bash -lc " + shlex.quote(script), stdin=passphrase + "\n", timeout=90).stdout
    try:
        sent = json.loads(stdout)
    except json.JSONDecodeError:
        sent = {"txid": stdout.strip()}
    return {
        "txid": sent.get("txid", ""),
        "fee_reason": sent.get("fee_reason"),
        "destination": clean_destination,
        "amount_btc": format(amount, "f"),
        "subtract_fee_from_amount": subtract_fee_from_amount,
        "conf_target": blocks,
        "estimate_mode": mode,
        "fee_rate_sat_vb": str(fee_rate) if fee_rate is not None else None,
    }
