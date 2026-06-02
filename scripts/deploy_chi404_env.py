"""Upload /root/hft3/.env on CHI404 from local .env (Rithmic + host keys)."""
from __future__ import annotations

import os
from pathlib import Path

import paramiko

HOST = os.environ.get("CHI404_HOST", "64.44.98.219")
USER = os.environ.get("CHI404_SSH_USER", "root")
KEY = Path.home() / ".ssh" / "hft3_chi404"
REMOTE_PATH = "/root/hft3/.env"
LOCAL_ENV = Path(__file__).resolve().parents[1] / ".env"

KEYS = (
    "HFT3_DEPLOY_HOST",
    "HFT3_PUBLIC_IP",
    "HFT3_ENV",
    "HFT3_REPO_DIR",
    "HOT_CPUS",
    "HFT3_ISOL_CPUS",
    "HFT3_RITHMIC_CPU",
    "HFT3_OS_CPU",
    "HFT3_RITHMIC_HOST",
    "HFT3_COLO_NTP",
    "HFT3_NIC",
    "HFT3_GATEWAY_IP",
    "RITHMIC_ENVIRONMENT",
    "RITHMIC_USERNAME",
    "RITHMIC_PASSWORD",
    "RITHMIC_APP_NAME",
    "RITHMIC_APP_VERSION",
    "RITHMIC_SYMBOL",
    "RITHMIC_EXCHANGE",
    "RTRADER_INSTALLER_PATH",
    "RTRADER_WINE_PREFIX",
    "RTRADER_WATCH_DIRS",
    "RITHMIC_GATEWAY",
    "RITHMIC_TRIAL_ENABLED",
    "RITHMIC_TRIAL_CONNECTOR",
    "RITHMIC_TRIAL_CONFIG",
)


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _render_env(values: dict[str, str]) -> str:
    defaults = {
        "HFT3_DEPLOY_HOST": "CHI404",
        "HFT3_PUBLIC_IP": HOST,
        "HFT3_ENV": "bare_metal_paper",
        "HFT3_REPO_DIR": "/root/hft3/repo",
        "HFT3_ISOL_CPUS": "2-11",
        "HFT3_RITHMIC_CPU": "1",
        "HFT3_OS_CPU": "0",
        "RITHMIC_ENVIRONMENT": "Rithmic Paper Trading",
        "RITHMIC_APP_NAME": "HFT3",
        "RITHMIC_APP_VERSION": "1.0",
        "RITHMIC_SYMBOL": "MES",
        "RITHMIC_EXCHANGE": "CME",
        "RITHMIC_GATEWAY": "Chicago",
        "RITHMIC_TRIAL_ENABLED": "1",
        "RITHMIC_TRIAL_CONNECTOR": "rithmic_api",
        "RITHMIC_TRIAL_CONFIG": "packages/data_system/config/rithmic_trial.yaml",
    }
    merged = {**defaults, **{k: values[k] for k in KEYS if k in values and values[k]}}
    lines = ["# HFT3 CHI404 — generated; chmod 600", ""]
    for k in KEYS:
        if k in merged and merged[k]:
            v = merged[k]
            if any(ch in v for ch in " #$(;"):
                v = v.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{k}="{v}"')
            else:
                lines.append(f"{k}={v}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    local = _parse_env(LOCAL_ENV)
    # Map common local names
    if "RITHMIC_USERNAME" not in local and local.get("RITHMIC_USER"):
        local["RITHMIC_USERNAME"] = local["RITHMIC_USER"]
    body = _render_env(local)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, key_filename=str(KEY), timeout=20)
    client.exec_command("mkdir -p /root/hft3")[1].channel.recv_exit_status()
    sftp = client.open_sftp()
    with sftp.open(REMOTE_PATH, "w") as f:
        f.write(body)
    sftp.chmod(REMOTE_PATH, 0o600)
    sftp.close()

    hook = "[ -f /root/hft3/.env ] && set -a && . /root/hft3/.env && set +a"
    for rc in ("/root/.bashrc", "/root/.profile"):
        cmd = f"grep -qF '/root/hft3/.env' {rc} 2>/dev/null || printf '%s\\n' '# HFT3' '{hook}' >> {rc}"
        client.exec_command(cmd)[1].channel.recv_exit_status()
    client.close()
    print(f"Wrote {REMOTE_PATH} on {HOST} and hooked shell startup.")


if __name__ == "__main__":
    main()
