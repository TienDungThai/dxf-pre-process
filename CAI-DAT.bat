@echo off
chcp 65001 >nul
title Cai dat dxfclean - chi chay 1 lan
echo ============================================
echo   CAI DAT DXFCLEAN - CHI CHAY 1 LAN
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua cai Python.
    echo.
    echo Tai tai: https://www.python.org/downloads/
    echo QUAN TRONG: khi cai nho TICK o "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

python --version
echo.
echo Dang cai dxfclean va thu vien can thiet, mat 2-5 phut...
echo.

python -m pip install --upgrade pip
python -m pip install "%~dp0."

echo.
if errorlevel 1 (
    echo [LOI] Cai that bai. Kiem tra ket noi mang hoac bao IT.
) else (
    echo [XONG] Da cai xong. Tu gio chi can chay CHAY.bat.
)
echo.
pause
