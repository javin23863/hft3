import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Test with "Chicago" system name and different URLs
test_script = '''
import asyncio
import ssl
from async_rithmic import RithmicClient

async def test(sysname, url, label):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_default_verify_paths()

    user = "michaeldixon3421"
    password = "af69cb2a"
    print(f"[{label}] sysname={sysname!r} url={url!r}", flush=True)

    client = RithmicClient(
        user=user, password=password, system_name=sysname,
        app_name="HFT3", app_version="1.0", url=url,
    )
    client.ssl_context = ctx
    try:
        await asyncio.wait_for(client.connect(), timeout=12)
        print(f"  [{label}] CONNECTED OK", flush=True)
        return True
    except asyncio.TimeoutError:
        print(f"  [{label}] TIMEOUT", flush=True)
    except Exception as e:
        msg = str(e)
        if "permission" in msg.lower():
            print(f"  [{label}] permission denied", flush=True)
        elif "system_name" in msg.lower():
            # Get valid list from error
            import re
            m = re.search(r"\\[(.+?)\\]", msg)
            if m:
                print(f"  [{label}] invalid. Valid: {m.group(1)}", flush=True)
            else:
                print(f"  [{label}] invalid system name", flush=True)
        else:
            print(f"  [{label}] {type(e).__name__}: {msg[:150]}", flush=True)
    finally:
        try: await client.disconnect()
        except: pass
    return False

async def main():
    tests = [
        ("Chicago", "wss://ritpz04063.04.rithmic.com:443", "chicago-cfg"),
        ("Chicago", "wss://rprotocol.rithmic.com:443", "chicago-rproto"),
        ("Rithmic Paper Trading", "wss://rprotocol.rithmic.com:443", "paper-rproto"),
        ("Rithmic 04 Colo", "wss://rprotocol.rithmic.com:443", "colo-rproto"),
    ]
    for sn, url, label in tests:
        await test(sn, url, label)
        await asyncio.sleep(1)

asyncio.run(main())
'''

sftp = client.open_sftp()
try:
    sftp.stat('/root/hft3/.tmp')
except IOError:
    sftp.mkdir('/root/hft3/.tmp')
with sftp.open('/root/hft3/.tmp/test_chicago.py', 'w') as f:
    f.write(test_script)
sftp.close()

cmd = 'cd /root/hft3 && python3 .tmp/test_chicago.py 2>&1'
print('=== Testing Chicago + Rithmic system names ===')
stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
print(stdout.read().decode(errors='replace'))
client.close()
