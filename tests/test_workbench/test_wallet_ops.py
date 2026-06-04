"""Wallet operation safety and behavior tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from workbench.src.run import wallet_ops


def test_parse_amount_has_no_workbench_cap() -> None:
    assert wallet_ops._parse_amount_btc("2.5") == Decimal("2.5")
    assert wallet_ops._parse_amount_btc("0.00000001") == Decimal("0.00000001")

    with pytest.raises(wallet_ops.WalletError, match="at least 1 satoshi"):
        wallet_ops._parse_amount_btc("0")
    with pytest.raises(wallet_ops.WalletError, match="valid BTC decimal"):
        wallet_ops._parse_amount_btc("not-btc")


def test_send_uses_single_stdin_passphrase_and_named_bitcoin_core_send(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_run_ssh(remote_command: str, *, stdin: str | None = None, timeout: int = 45) -> wallet_ops.CommandResult:
        captured["remote_command"] = remote_command
        captured["stdin"] = stdin
        captured["timeout"] = str(timeout)
        return wallet_ops.CommandResult(stdout='{"txid":"abc123","fee_reason":"fallback"}', stderr="")

    monkeypatch.setattr(wallet_ops, "_run_ssh", fake_run_ssh)

    result = wallet_ops.send_btc(
        "bc1qexampledestination",
        "1.25",
        "secret-passphrase",
        subtract_fee_from_amount=True,
        conf_target=2,
        estimate_mode="conservative",
        fee_rate_sat_vb="12.5",
    )

    assert result["txid"] == "abc123"
    assert captured["stdin"] == "secret-passphrase\n"
    command = str(captured["remote_command"])
    assert "-named sendtoaddress" in command
    assert "subtractfeefromamount" in command
    assert "fee_rate" in command
    assert "walletlock" in command
    assert "secret-passphrase" not in command


def test_preview_builds_unsigned_funded_psbt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_remote_json(script: str, *, stdin: str | None = None, timeout: int = 45) -> dict[str, object]:
        captured["script"] = script
        captured["stdin"] = stdin
        captured["timeout"] = str(timeout)
        return {"can_fund": True, "network_fee_btc": 0.00001}

    monkeypatch.setattr(wallet_ops, "_remote_python_json", fake_remote_json)

    result = wallet_ops.preview_send("bc1qexampledestination", "3.0", fee_rate_sat_vb="10")

    assert result["can_fund"] is True
    assert captured["stdin"] is None
    assert "walletcreatefundedpsbt" in str(captured["script"])
    assert "from decimal import Decimal" in str(captured["script"])
    assert "walletpassphrase" not in str(captured["script"])
    assert "sendtoaddress" not in str(captured["script"])
