@echo off
rem sdd-stock-diagnosis MVP server launcher (T-09)
rem Python venv: managed isolated environment (akshare 1.18.64 locked)
cd /d %~dp0..
set SDD_POOL_MODE=demo
set SDD_DAILY_LIMIT=50
set SDD_PORT=8000
"C:\Users\28309\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn src.app:app --host 0.0.0.0 --port 8000
