import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Test with "Rithmic 04 Colo" system name
test_script = '''
import asyncio
import ssl
import os
from async_rithmic import RithmicClient

async def test(sysname):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_default_verify_paths()

    user = "michaeldixon3421"
    password = "af69cb2a"
    url = "wss://ritpz04063.04.rithmic.com:443"
    print(f"Testing system_name={sysname!r}", flush=True)

    client = RithmicClient(
        user=user, password=password, system_name=sysname,
        app_name="HFT3", app_version="1.0", url=url,
    )
    client.ssl_context = ctx
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        print(f"  {sysname}: CONNECTED OK", flush=True)
    except asyncio.TimeoutError:
        print(f"  {sysname}: TIMEOUT", flush=True)
    except Exception as e:
        msg = str(e)
        if "permission" in msg.lower():
            print(f"  {sysname}: permission denied", flush=True)
        elif "system_name" in msg.lower() or "valid" in msg.lower():
            print(f"  {sysname}: invalid system name", flush=True)
        else:
            print(f"  {sysname}: {type(e).__name__}: {msg[:100]}", flush=True)
    finally:
        try: await client.disconnect()
        except: pass

async def main():
    for sn in ["Rithmic Paper Trading", "Rithmic 04 Colo", "Rithmic 01"]:
        await test(sn)
        await asyncio.sleep(1)

asyncio.run(main())
'''

sftp = client.open_sftp()
try:
    sftp.stat('/root/hft3/.tmp')
except IOError:
    sftp.mkdir('/root/hft3/.tmp')
with sftp.open('/root/hft3/.tmp/test_systems.py', 'w') as f:
    f.write(test_script)
sftp.close()

cmd = 'cd /root/hft3 && python3 .tmp/test_systems.py 2>&1'
print('=== Testing multiple system names ===')
stdin, stdout, stderr = client.exec_command(cmd, timeout=90)
print(stdout.read().decode(errors='replace'))
client.close()
