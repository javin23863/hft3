#!/bin/bash

# Chicago CME Microstructure - Bare-Metal Kernel Tuning Script (Strict)
# Must be run as root
# Requires: tuned, linux-tools, numactl

echo "Configuring STRICT CPU isolation and C-states..."

# Define core isolation (Assume a 16-core CPU. Isolate cores 2-15 on NUMA node 0)
# Core 0: OS
# Core 1: Rithmic Gateway (non-isolated to handle networking interrupts if needed, or isolate it depending on NIC)
# Cores 2-15: Strategy, Risk, Features
ISOL_CORES="2-15"

# 1. Update GRUB configuration
GRUB_FILE="/etc/default/grub"
BACKUP_FILE="/etc/default/grub.bak.$(date +%F)"

if [ -f "$GRUB_FILE" ]; then
    cp $GRUB_FILE $BACKUP_FILE
    
    # Check if we already added our parameters
    if ! grep -q "isolcpus=$ISOL_CORES" $GRUB_FILE; then
        # Strict isolation: 
        # - isolcpus: completely remove from scheduler
        # - nohz_full: disable tick timer on these cores
        # - rcu_nocbs: offload RCU callbacks
        # - processor.max_cstate=0 intel_idle.max_cstate=0 amd_idle.max_cstate=0 cpuidle.off=1: Disable deep sleep
        # - mce=ignore_ce: ignore corrected machine check errors (prevents SMI-like stalls)
        # - audit=0 nmi_watchdog=0: disable kernel watchdogs that cause latency spikes
        STRICT_ARGS="isolcpus=$ISOL_CORES nohz_full=$ISOL_CORES rcu_nocbs=$ISOL_CORES processor.max_cstate=0 intel_idle.max_cstate=0 amd_idle.max_cstate=0 cpuidle.off=1 mce=ignore_ce audit=0 nmi_watchdog=0 nosoftlockup"
        
        sed -i "s/GRUB_CMDLINE_LINUX_DEFAULT=\"[^\"]*/& $STRICT_ARGS/" $GRUB_FILE
        
        echo "Updating GRUB... (Will require reboot)"
        update-grub
    else
        echo "GRUB already configured with isolation parameters."
    fi
else
    echo "ERROR: $GRUB_FILE not found."
    exit 1
fi

# 2. Network and IRQ tuning
echo "Disabling irqbalance..."
systemctl stop irqbalance
systemctl disable irqbalance

# Set CPU scaling governor to performance on all cores
if command -v cpupower &> /dev/null; then
    cpupower frequency-set -g performance
else
    echo "cpupower not installed. Governor may not be set to performance."
fi

# 3. Disable HyperThreading (Requires sysfs access, preferable in BIOS)
echo "Attempting to disable SMT/HyperThreading..."
echo off > /sys/devices/system/cpu/smt/control 2>/dev/null || echo "Could not disable SMT via sysfs (check BIOS)"

echo "Kernel tuning configured. A reboot is REQUIRED to apply GRUB changes."
