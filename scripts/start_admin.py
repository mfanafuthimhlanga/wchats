import os, subprocess, sys
from pathlib import Path

os.chdir(r"C:\Users\Bantu\mzansi-agentive\veridian\apps\admin")
# Find npm.cmd
npm = r"C:\Program Files\nodejs\npm.cmd"
sys.exit(subprocess.call([npm, "run", "dev"]))
