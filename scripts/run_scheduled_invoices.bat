@echo off
cd /d "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
call .venv\Scripts\activate.bat
python scripts\run_scheduled_invoices.py --saida-json logs\lote_%date:~-4,4%-%date:~-7,2%-%date:~-10,2%.json
