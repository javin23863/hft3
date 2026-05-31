# CHI404 access paths (not just SSH)

**Host:** `64.44.98.219` (CHI404) · **Colo:** QuantVPS · **BMC (in-band):** `10.10.91.93`

SSH is **one** management path. When Linux is down (BIOS setup), use **QuantVPS IPMI/KVM** or a **routable BMC IP** — not SSH.

## Path matrix

| Path | Needs OS/SSH? | Port | Tool | When to use |
|------|---------------|------|------|-------------|
| **SSH** | Yes | 22 | `ssh chi404` | Normal ops, scripts, sync |
| **QuantVPS dashboard** | **No** | (portal) | https://www.quantvps.com/login | Credentials, support; bare-metal console via QuantVPS support |
| **iKVM (AST2600)** | Jump via SSH | 443 → localhost:8443 | `run_chi404_bmc_ikvm_tunnel.ps1` | BIOS/EXPO when Linux up |
| **IPMI lanplus** | Jump via SSH *or* public BMC IP | 623 → localhost:1623 | `run_chi404_bmc_ipmi_tunnel.ps1` + ipmitool | Power, bootdev, SOL |
| **Redfish API** | Jump via SSH *or* public BMC IP | 443 | curl / `24_recover_boot_to_disk.sh` | BIOS settings, boot override, reset |
| **SOL (serial BIOS)** | IPMI reachable | 623 | `ipmitool sol activate` | Keyboard automation in BIOS |
| **KCS (local IPMI)** | On box only | — | `ipmitool` on CHI404 | When already SSH'd in |

## Recovery priority when SSH is down

1. **QuantVPS** — log in at https://www.quantvps.com/login → open server → support ticket for console/IPMI (CHI404 bare metal is not standard Windows RDP VPS)  
2. **Direct BMC** — if QuantVPS assigned `HFT3_BMC_PUBLIC_IP`, run `scripts/run_chi404_bmc_redfish_recovery.ps1`  
3. **Wait for POST** — then SSH poll runs `24_recover_boot_to_disk.sh` automatically  

## Credentials

| System | User | Where stored |
|--------|------|--------------|
| SSH | `root` | `~/.ssh/hft3_chi404` |
| BMC / iKVM / Redfish | `admin` | `/root/hft3/.env` → `HFT3_BMC_PASSWORD` on box; copy to workstation `.env` for direct BMC scripts |

## Scripts

```powershell
# All paths (tries SSH, direct Redfish, opens QuantVPS)
powershell -File scripts/run_chi404_oob_recovery.ps1

# When SSH is up
powershell -File scripts/run_chi404_bmc_ikvm_tunnel.ps1
powershell -File scripts/run_chi404_bmc_ipmi_tunnel.ps1

# Direct Redfish boot-to-disk (needs routable BMC IP in .env)
powershell -File scripts/run_chi404_bmc_redfish_recovery.ps1
```

Authority: [CPU_MEMORY_OVERCLOCK.md](CPU_MEMORY_OVERCLOCK.md) · [HARDWARE_BASELINE.md](HARDWARE_BASELINE.md)
