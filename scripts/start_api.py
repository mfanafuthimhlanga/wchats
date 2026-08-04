import os, subprocess, sys
from pathlib import Path

env_file = Path(r"C:\Users\Bantu\mzansi-agentive\veridian\apps\api\.env")
env = os.environ.copy()
for line in env_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        env[k.strip()] = v.strip()

os.chdir(r"C:\Users\Bantu\mzansi-agentive\veridian\apps\api")
sys.exit(subprocess.call(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
    env=env
))
