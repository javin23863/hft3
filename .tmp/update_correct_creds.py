import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Update .env with CORRECT Rithmic Test credentials
# Per Rithmic support: Joshuajacob2386@gmail.com / 5C.HfOrV
# Rithmic Test environment only
sftp = client.open_sftp()
env_lines = [
    'RITHMIC_USERNAME=Joshuajacob2386@gmail.com',
    'RITHMIC_PASSWORD=5C.HfOrV',
    'RITHMIC_APP_NAME=HFT3',
    'RITHMIC_APP_VERSION=1.0',
    'RITHMIC_SYSTEM_NAME="Rithmic Test"',
    'RITHMIC_URL=wss://rituz00100.00.rithmic.com:443',
    'HFT3_RITHMIC_HOST=rituz00100.00.rithmic.com',
    'RITHMIC_GATEWAY=Chicago',
    'RITHMIC_ENVIRONMENT="Rithmic Test"',
    'HFT3_REPO_DIR=/root/hft3',
]
with sftp.open('/root/hft3/.env', 'w') as f:
    f.write('\n'.join(env_lines) + '\n')
sftp.chmod('/root/hft3/.env', 0o600)
sftp.close()
print('.env updated with correct Rithmic Test credentials')

# Verify
stdin, stdout, stderr = client.exec_command('cat /root/hft3/.env', timeout=10)
print(stdout.read().decode(errors='replace'))

client.close()
