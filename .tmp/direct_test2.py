import paramiko
import time

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Use the .env file directly
sftp = client.open_sftp()
sftp.close()

# Direct async_rithmic test - write a script on CHI404 to avoid quoting issues
test_script = '''
import asyncio
import ssl
import os
from async_rithmic import RithmicClient

async def test():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_default_verify_paths()

    user = os.environ.get("RITHMIC_USERNAME", "")
    password = os.environ.get("RITHMIC_PASSWORD", "")
    system_name = os.environ.get("RITHMIC_SYSTEM_NAME", "Rithmic Paper Trading")
    url = os.environ.get("RITHMIC_URL", "wss://ritpz04063.04.rithmic.com:443")
    print(f"user={user!r}")
    print(f"system_name={system_name!r}")
    print(f"url={url!r}")
    print(f"password set: {bool(password)}")

    client = RithmicClient(
        user=user,
        password=password,
        system_name=system_name,
        app_name="HFT3",
        app_version="1.0",
        url=url,
    )
    client.ssl_context = ctx
    try:
        await client.connect()
        print("CONNECTED OK")
        from datetime import datetime, timezone, timedelta
        end = datetime(2020, 1, 10, 14, 30, tzinfo=timezone.utc)
        start = end - timedelta(minutes=1)
        ticks = await client.get_historical_tick_data(
            symbol="MES",
            exchange="CME",
            start_time=start,
            end_time=end,
            max_pages=1,
        )
        print(f"Got {len(ticks) if isinstance(ticks, list) else type(ticks)} ticks")
        if isinstance(ticks, list) and ticks:
            t0 = ticks[0]
            print("First tick type:", type(t0))
            if isinstance(t0, dict):
                print("First tick keys:", list(t0.keys()))
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        try:
            await client.disconnect()
        except: pass

asyncio.run(test())
'''

# Write test script to CHI404
sftp = client.open_sftp()
try:
    sftp.stat('/root/hft3/.tmp')
except IOError:
    sftp.mkdir('/root/hft3/.tmp')
with sftp.open('/root/hft3/.tmp/test_rithmic.py', 'w') as f:
    f.write(test_script)
sftp.close()

cmd = '''
cd /root/hft3
set -a
. /root/hft3/.env
set +a
python3 .tmp/test_rithmic.py 2>&1
'''
print('=== Direct async_rithmic test ===')
stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
print(stdout.read().decode(errors='replace'))
print('STDERR:', stderr.read().decode(errors='replace'))

client.close()
