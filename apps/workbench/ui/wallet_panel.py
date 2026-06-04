"""Operator wallet panel for Bitcoin Core operations."""

from __future__ import annotations

from io import BytesIO
import time
from decimal import Decimal
from typing import Any

import pandas as pd
import qrcode
import streamlit as st

from workbench.src.run.wallet_ops import (
    DEFAULT_CONFIRMATION_TARGET,
    WalletError,
    create_receive_address,
    preview_send,
    recent_transactions,
    send_btc,
    wallet_snapshot,
)


def _btc(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):.8f} BTC"
    except Exception:
        return "0.00000000 BTC"


def _trusted_balance(balances: dict[str, Any]) -> Any:
    return ((balances or {}).get("mine") or {}).get("trusted", 0)


def _pending_balance(balances: dict[str, Any]) -> Any:
    mine = (balances or {}).get("mine") or {}
    return Decimal(str(mine.get("untrusted_pending", 0))) + Decimal(str(mine.get("immature", 0)))


def _receive_qr_png(address: str) -> bytes:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(f"bitcoin:{address}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _clear_passphrase_widgets() -> None:
    for key in list(st.session_state):
        if str(key).startswith("wb_wallet_passphrase_"):
            del st.session_state[key]
    st.session_state.wb_wallet_passphrase_nonce = st.session_state.get("wb_wallet_passphrase_nonce", 0) + 1


def _send_request(
    destination: str,
    amount: str,
    subtract_fee_from_amount: bool,
    conf_target: int,
    estimate_mode: str,
    fee_rate_sat_vb: str,
) -> dict[str, Any]:
    return {
        "destination": destination.strip(),
        "amount_btc": amount.strip(),
        "subtract_fee_from_amount": bool(subtract_fee_from_amount),
        "conf_target": int(conf_target),
        "estimate_mode": estimate_mode,
        "fee_rate_sat_vb": fee_rate_sat_vb.strip() or None,
    }


def _render_snapshot(snapshot: dict[str, Any]) -> None:
    hot = snapshot.get("hot_wallet", {})
    watch = snapshot.get("watch_only_wallet", {})
    node = snapshot.get("node", {})

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spendable", _btc(_trusted_balance(hot.get("balances", {}))))
    c2.metric("Pending", _btc(_pending_balance(hot.get("balances", {}))))
    c3.metric("Wallet lock", "Locked" if int(hot.get("unlocked_until") or 0) == 0 else "Unlocked")
    c4.metric("Peers", node.get("connections", 0))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Chain", node.get("chain", "unknown"))
    c6.metric("Blocks", node.get("blocks", 0))
    c7.metric("Mempool", "Loaded" if node.get("mempool_loaded") else "Loading")
    c8.metric("Watch-only", "No private keys" if not watch.get("private_keys_enabled") else "CHECK")
    refreshed_at = st.session_state.get("wb_wallet_refreshed_at")
    refresh_seconds = st.session_state.get("wb_wallet_refresh_seconds")
    if refreshed_at and refresh_seconds is not None:
        st.caption(f"Last wallet refresh: {refreshed_at} ({refresh_seconds:.1f}s)")


def _render_receive() -> None:
    st.subheader("Receive")
    st.caption("Generate a fresh bech32 address from the hot wallet. No wallet unlock is required.")
    with st.form("wallet_receive_form"):
        label = st.text_input("Address label", value="ops-test")
        submitted = st.form_submit_button("Create Receive Address", type="primary")
    if submitted:
        try:
            with st.spinner("Creating receive address on btc-node..."):
                result = create_receive_address(label)
            if result.get("isvalid") and result.get("ismine"):
                st.session_state.wb_wallet_last_receive = result
                st.success("Receive address ready.")
            else:
                st.warning("Address was created but validation did not return the expected wallet ownership flags.")
                st.json(result)
        except WalletError as exc:
            st.error(str(exc))
    result = st.session_state.get("wb_wallet_last_receive")
    if result:
        st.caption("Latest receive address")
        st.code(result["address"], language="text")
        st.image(_receive_qr_png(result["address"]), caption="Scan to receive BTC", width=280)


def _render_send() -> None:
    st.subheader("Send")
    st.caption(
        "Manual operator broadcast through Bitcoin Core. There is no Workbench amount cap; Bitcoin Core enforces address validity, spendable balance, and fee funding."
    )

    send_message = st.session_state.get("wb_wallet_send_message")
    if send_message:
        level, payload = send_message
        if level == "success":
            st.success("Transaction submitted and wallet relocked.")
            st.code(payload["txid"], language="text")
        elif level == "error":
            st.error(payload)

    preview = st.session_state.get("wb_wallet_send_preview")
    if preview:
        if preview.get("can_fund"):
            st.success("Preview funded by wallet.")
        else:
            st.warning("Preview is not fundable yet.")
        fields = {
            "Destination": preview.get("destination", ""),
            "Amount BTC": preview.get("amount_btc", ""),
            "Network fee BTC": preview.get("network_fee_btc", "unknown"),
            "Total debit BTC": preview.get("total_debit_btc", "unknown"),
            "Spendable BTC": preview.get("spendable_btc", "unknown"),
            "RBF": "true",
            "Fee mode": preview.get("estimate_mode", ""),
        }
        st.dataframe(pd.DataFrame([fields]), width="stretch", hide_index=True)
        if preview.get("error"):
            st.caption(str(preview["error"]))

    if st.session_state.get("wb_wallet_last_send"):
        result = st.session_state.wb_wallet_last_send
        st.success("Latest transaction submitted and wallet relocked.")
        st.code(result["txid"], language="text")

    passphrase_key = f"wb_wallet_passphrase_{st.session_state.get('wb_wallet_passphrase_nonce', 0)}"
    with st.form("wallet_send_form"):
        destination = st.text_input("Destination address")
        amount = st.text_input("Amount BTC", value="0.0001")
        subtract_fee_from_amount = st.checkbox("Subtract network fee from sent amount")
        conf_target = st.number_input("Confirmation target blocks", min_value=1, max_value=1008, value=DEFAULT_CONFIRMATION_TARGET, step=1)
        estimate_mode = st.selectbox("Fee estimate mode", ["economical", "conservative", "unset"])
        fee_rate_sat_vb = st.text_input("Manual fee rate sat/vB", value="")
        passphrase = st.text_input("Wallet passphrase", type="password", key=passphrase_key)
        confirm_preview = st.checkbox("I reviewed the destination, amount, and fee preview.")
        confirm_broadcast = st.checkbox("Broadcast this Bitcoin transaction now.")
        preview_submitted = st.form_submit_button("Preview Transaction")
        send_submitted = st.form_submit_button("Broadcast BTC", type="primary")

    request = _send_request(destination, amount, subtract_fee_from_amount, int(conf_target), estimate_mode, fee_rate_sat_vb)
    if preview_submitted:
        try:
            with st.spinner("Building unsigned transaction preview..."):
                result = preview_send(**request)
            result["request"] = request
            st.session_state.wb_wallet_send_preview = result
            st.session_state.wb_wallet_send_message = None
            _clear_passphrase_widgets()
            st.rerun(scope="fragment")
        except WalletError as exc:
            st.session_state.wb_wallet_send_message = ("error", str(exc))
            _clear_passphrase_widgets()
            st.rerun(scope="fragment")

    if send_submitted:
        try:
            current_preview = st.session_state.get("wb_wallet_send_preview") or {}
            if current_preview.get("request") != request:
                raise WalletError("Preview this exact destination, amount, and fee setting before broadcast.")
            if not current_preview.get("can_fund"):
                raise WalletError("The latest preview is not fundable by the wallet.")
            if not (confirm_preview and confirm_broadcast):
                raise WalletError("Both broadcast confirmations are required.")
            with st.spinner("Submitting transaction through Bitcoin Core..."):
                result = send_btc(passphrase=passphrase, **request)
            st.session_state.wb_wallet_last_send = result
            st.session_state.wb_wallet_send_message = ("success", result)
            st.session_state.wb_wallet_send_preview = None
        except WalletError as exc:
            st.session_state.wb_wallet_send_message = ("error", str(exc))
        finally:
            _clear_passphrase_widgets()
            st.rerun(scope="fragment")


def _render_activity(rows: list[dict[str, Any]] | None) -> None:
    st.subheader("Recent Activity")
    if rows is None:
        st.info("Click Refresh Activity to load recent hot-wallet transactions from btc-node.")
        return
    if rows:
        frame = pd.DataFrame(rows)
        keep = [col for col in ("time", "category", "address", "amount", "confirmations", "txid") if col in frame.columns]
        st.dataframe(frame[keep], width="stretch", hide_index=True)
    else:
        st.info("No wallet transactions observed yet.")


@st.fragment
def render_wallet_panel() -> None:
    st.header("Wallet")
    st.warning("Operational hot wallet only. Do not use this node wallet for meaningful long-term BTC custody.")

    c1, c2 = st.columns([1, 1])
    if c1.button("Refresh Wallet", type="primary"):
        try:
            start = time.perf_counter()
            with st.spinner("Refreshing wallet from btc-node..."):
                st.session_state.wb_wallet_snapshot = wallet_snapshot()
            st.session_state.wb_wallet_refresh_seconds = time.perf_counter() - start
            st.session_state.wb_wallet_refreshed_at = time.strftime("%H:%M:%S")
        except WalletError as exc:
            st.error(str(exc))
    if c2.button("Refresh Activity"):
        try:
            with st.spinner("Loading recent wallet activity..."):
                st.session_state.wb_wallet_activity = recent_transactions()
        except WalletError as exc:
            st.error(str(exc))

    snapshot = st.session_state.get("wb_wallet_snapshot")
    if snapshot:
        _render_snapshot(snapshot)
    else:
        st.info("Click Refresh Wallet to load balance, lock state, and node status from btc-node.")

    left, right = st.columns(2)
    with left:
        _render_receive()
    with right:
        _render_send()
    _render_activity(st.session_state.get("wb_wallet_activity"))
