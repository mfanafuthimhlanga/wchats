@echo off
for /f "usebackq tokens=1,* delims==" %%A in ("C:\Users\Bantu\mzansi-agentive\veridian\apps\api\.env") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
)
cd /d C:\Users\Bantu\mzansi-agentive\veridian\apps\api
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
