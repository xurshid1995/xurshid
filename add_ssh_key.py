import paramiko
import os
from dotenv import load_dotenv

load_dotenv()

SERVER_HOST = os.getenv('SERVER_HOST', '')
SERVER_PASSWORD = os.getenv('SERVER_PASSWORD', '')
if not SERVER_HOST or not SERVER_PASSWORD:
    raise SystemExit(
        "XAVFSIZLIK: SERVER_HOST va SERVER_PASSWORD .env faylida belgilanishi kerak."
    )

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

# 1. Parol bilan ulanish
try:
    print("1) Parol bilan ulanilmoqda (port 2222)...")
    ssh.connect(SERVER_HOST, port=2222, username='root', password=SERVER_PASSWORD, timeout=20, allow_agent=False, look_for_keys=False)
    print("Ulandi!")
    stdin, stdout, stderr = ssh.exec_command('echo OK')
    print(f"Test: {stdout.read().decode().strip()}")
    ssh.close()
    print("Parol bilan ulanish muvaffaqiyatli!")
except Exception as e:
    print(f"Parol bilan xato: {e}")

# 2. Kalit bilan ulanish
try:
    ssh2 = paramiko.SSHClient()
    ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("\n2) Kalit bilan ulanilmoqda (port 2222)...")
    key_path = os.path.expanduser('~/.ssh/id_rsa')
    pkey = paramiko.RSAKey.from_private_key_file(key_path)
    ssh2.connect(SERVER_HOST, port=2222, username='root', pkey=pkey, timeout=20, allow_agent=False, look_for_keys=False)
    print("Kalit bilan ulandi!")
    stdin, stdout, stderr = ssh2.exec_command('echo OK && hostname')
    print(f"Test: {stdout.read().decode().strip()}")
    ssh2.close()
    print("Kalit bilan ulanish muvaffaqiyatli!")
except Exception as e:
    print(f"Kalit bilan xato: {e}")

# 3. Port 22 ham sinab ko'rish
try:
    ssh3 = paramiko.SSHClient()
    ssh3.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("\n3) Port 22 parol bilan ulanilmoqda...")
    ssh3.connect(SERVER_HOST, port=22, username='root', password=SERVER_PASSWORD, timeout=15, allow_agent=False, look_for_keys=False)
    print("Port 22 ulandi!")
    stdin, stdout, stderr = ssh3.exec_command('echo OK')
    print(f"Test: {stdout.read().decode().strip()}")
    ssh3.close()
except Exception as e:
    print(f"Port 22 xato: {e}")
