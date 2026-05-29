#!/bin/bash

# Chicago CME Microstructure - Pre-production latency report generator

REPORT_FILE="latency_report_$(date +%F_%H%M%S).txt"

echo "Generating Pre-production Latency Report..." | tee $REPORT_FILE
echo "=========================================" | tee -a $REPORT_FILE
echo "Timestamp: $(date)" | tee -a $REPORT_FILE
echo "" | tee -a $REPORT_FILE

# 1. CPU Steal and System Load
echo "[CPU Steal & Load Average]" | tee -a $REPORT_FILE
uptime | tee -a $REPORT_FILE
mpstat 1 5 | awk '/Average:/ {print "Average CPU Steal: " $13 "%"}' | tee -a $REPORT_FILE
echo "" | tee -a $REPORT_FILE

# 2. Clock Sync tracking
echo "[Clock Sync Status]" | tee -a $REPORT_FILE
chronyc tracking | tee -a $REPORT_FILE
echo "" | tee -a $REPORT_FILE
chronyc sources | tee -a $REPORT_FILE
echo "" | tee -a $REPORT_FILE

# 3. Disk Latency (for async logging)
echo "[Disk Latency (iostat)]" | tee -a $REPORT_FILE
# Checking basic await/svctm via iostat
if command -v iostat &> /dev/null; then
    iostat -dx 1 5 | awk 'BEGIN{print "Device\tr/s\tw/s\tawait"} /^[a-z]/ {print $1 "\t" $4 "\t" $5 "\t" $10}' | tail -n 5 | tee -a $REPORT_FILE
else
    echo "sysstat package not installed (iostat missing)." | tee -a $REPORT_FILE
fi
echo "" | tee -a $REPORT_FILE

# 4. Network Latency to generic Chicago IPs (Replace with Rithmic Gateway IP when available)
# Using a common Chicago ping target as placeholder
TEST_IP="8.8.8.8" # Replace with Rithmic IP
echo "[Network Latency to $TEST_IP (Placeholder for Rithmic)]" | tee -a $REPORT_FILE
ping -c 20 -i 0.2 -q $TEST_IP | tee -a $REPORT_FILE
echo "" | tee -a $REPORT_FILE

echo "Report generated at $REPORT_FILE"
