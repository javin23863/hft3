import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Try all prop firm system names
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
    c = RithmicClient(
        user=user, password=password, system_name=sysname,
        app_name="HFT3", app_version="1.0", url=url,
    )
    c.ssl_context = ctx
    try:
        await asyncio.wait_for(c.connect(), timeout=10)
        print(f"  [{label}] CONNECTED OK!", flush=True)
        return True
    except asyncio.TimeoutError:
        print(f"  [{label}] TIMEOUT", flush=True)
    except Exception as e:
        msg = str(e)
        if "permission" in msg.lower():
            print(f"  [{label}] permission denied", flush=True)
        else:
            print(f"  [{label}] {type(e).__name__}: {msg[:80]}", flush=True)
    finally:
        try: await c.disconnect()
        except: pass
    return False

async def main():
    # All prop firm / Rithmic systems from the valid list
    systems = [
        "Rithmic Paper Trading", "Rithmic 01", "Rithmic 04 Colo",
        "TopstepTrader", "Apex", "TradeFundrr", "MES Capital",
        "TheTradingPit", "FundedFuturesNetwork", "PropShopTrader",
        "4PropTrader", "DayTraders.com", "LucidTrading",
        "ThriveTrading", "LegendsTrading", "Earn2Trade",
        "YPF-t", "tradesea", "tradesea-d",
    ]
    for sn in systems:
        ok = await test(sn, "wss://rprotocol.rithmic.com:443", sn)
        if ok:
            print(f"\\n*** WINNER: {sn} ***", flush=True)
            return
        await asyncio.sleep(0.5)

asyncio.run(main())
'''

sftp = client.open_sftp()
try:
    sftp.stat('/root/hft3/.tmp')
except IOError:
    sftp.mkdir('/root/hft3/.tmp')
with sftp.open('/root/hft3/.tmp/test_all_systems.py', 'w') as f:
    f.write(test_script)
sftp.close()

cmd = 'cd /root/hft3 && python3 .tmp/test_all_systems.py 2>&1'
print('=== Testing all Rithmic system names ===')
stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
out = stdout.read().decode(errors='replace')
print(out)
client.close()
