import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

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
    system_name = os.environ.get("RITHMIC_SYSTEM_NAME", "")
    url = os.environ.get("RITHMIC_URL", "")
    print(f"user={user!r}", flush=True)
    print(f"system_name={system_name!r}", flush=True)
    print(f"url={url!r}", flush=True)
    print(f"password len={len(password)}", flush=True)

    client = RithmicClient(
        user=user, password=password, system_name=system_name,
        app_name="HFT3", app_version="1.0", url=url,
    )
    client.ssl_context = ctx
    try:
        print("Connecting...", flush=True)
        await asyncio.wait_for(client.connect(), timeout=30)
        print("CONNECTED", flush=True)
    except asyncio.TimeoutError:
        print("TIMEOUT", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        try: await client.disconnect()
        except: pass

asyncio.run(test())
'''

sftp = client.open_sftp()
try:
    sftp.stat('/root/hft3/.tmp')
except IOError:
    sftp.mkdir('/root/hft3/.tmp')
with sftp.open('/root/hft3/.tmp/test_rithmic.py', 'w') as f:
    f.write(test_script)
sftp.close()

cmd = 'cd /root/hft3 && set -a && . /root/hft3/.env && set +a && python3 .tmp/test_rithmic.py 2>&1'
print('=== Running test ===')
stdin, stdout, stderr = client.exec_command(cmd, timeout=90)
print(stdout.read().decode(errors='replace'))
print('STDERR:', stderr.read().decode(errors='replace'))
client.close()
