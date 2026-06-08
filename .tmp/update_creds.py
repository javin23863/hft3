import paramiko

HOST = '64.44.98.219'
USER = 'root'
PASS = '90m_AIpO__9t^m'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)
print('CONNECTED')

# Update .env with new Rithmic credentials
sftp = client.open_sftp()
env_lines = [
    'RITHMIC_USERNAME=michaeldixon3421',
    'RITHMIC_PASSWORD=af69cb2a',
    'RITHMIC_APP_NAME=HFT3',
    'RITHMIC_APP_VERSION=1.0',
    'RITHMIC_SYSTEM_NAME="Rithmic Paper Trading"',
    'RITHMIC_URL=wss://ritpz04063.04.rithmic.com:443',
    'HFT3_RITHMIC_HOST=ritpz04063.04.rithmic.com',
    'RITHMIC_GATEWAY=Chicago',
    'RITHMIC_ENVIRONMENT=Rithmic Paper Trading',
    'HFT3_REPO_DIR=/root/hft3',
]
with sftp.open('/root/hft3/.env', 'w') as f:
    f.write('\n'.join(env_lines) + '\n')
sftp.chmod('/root/hft3/.env', 0o600)
sftp.close()
print('.env updated with michaeldixon3421 credentials')

# Verify
stdin, stdout, stderr = client.exec_command('cat /root/hft3/.env', timeout=10)
print(stdout.read().decode(errors='replace'))

client.close()
