# Deprecated CHI404 R|Trader experiments

These scripts are **not** part of the supported path. Kept for reference only.

Supported path: Windows VM on CHI404 + SMB share → `/root/hft3/rtrader_watch` (see `docs/rithmic_trial/README.md`).

- `chi404_setup_wine32.sh` — Wine/dotnet on Linux (failed: mscoree / .NET 4.7.2)
- `chi404_install_dotnet472.sh`, `chi404_install_dotnet472_clean.sh` — dotnet472 winetricks (hangs)
- `chi404_seed_wine_from_windows.sh` — copying Windows Framework DLLs (CLR still fails)
- `chi404_rtrader_auto_login.sh`, `chi404_rtrader_vnc_login.sh` — Wine VNC login helpers
- `chi404_setup_log_bridge.sh`, `push_rtrader_logs_chi404.ps1` — workstation log-push (forbidden by AGENTS.md)
