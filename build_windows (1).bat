@echo off
REM ============================================================
REM  Dong goi PRO AUTO POSTER thanh file .exe (chay tren Windows)
REM  Cach dung: double-click file nay
REM ============================================================
cd /d "%~dp0"

echo [1/2] Cai dat pyinstaller...
pip install pyinstaller requests

echo [2/2] Dang build, cho mot chut...
python -m PyInstaller --noconfirm --onefile --windowed --name "ProAutoPoster" main.py

echo.
echo ============================================================
echo  XONG! File .exe nam o: dist\ProAutoPoster.exe
echo  (Copy file do di dau cung chay duoc, khong can cai Python)
echo ============================================================
pause
