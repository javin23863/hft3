import paramiko
import time
import sys

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Run the full download for 4 priority event types
cmd = (
    'cd /root/hft3 && '
    'set -a && . /root/hft3/.env && set +a && '
    'python3 scripts/download_mbo_release_data.py '
    '--download --source rithmic_api --derive-npz '
    '--only-event-type CPI --only-event-type JOLTS '
    '--only-event-type NFP --only-event-type PCE '
    '--workers 4 '
    '--output /root/hft3/runtime/data_downloads/chi404_rithmic_run.json '
    '2>&1'
)
print(f'=== Running: {cmd} ===')
print('This will take a while (downloading Rithmic historical data)...')

# Use a longer timeout for the actual download
stdin, stdout, stderr = client.exec_command(cmd, timeout=7200)  # 2 hours

# Stream output
import select
channel = stdout.channel
start = time.time()
last_print = time.time()
while True:
    if channel.recv_ready():
        data = channel.recv(4096).decode(errors='replace')
        sys.stdout.write(data)
        sys.stdout.flush()
        last_print = time.time()
    if channel.exit_status_ready():
        break
    if time.time() - last_print > 300:
        print('...still running (no output for 5min)...', flush=True)
        last_print = time.time()
    time.sleep(1)

# Get any remaining output
remaining = stdout.read().decode(errors='replace')
if remaining:
    print(remaining)

exit_code = channel.recv_exit_status()
print(f'\n=== Exit code: {exit_code} ===')
client.close()
print('DONE')
