@echo off
chcp 65001 >nul
title dxfclean - Don file cho may cat laser
setlocal enabledelayedexpansion

set "OUT=%~dp0ket-qua"

echo ============================================
echo   DXFCLEAN - DON FILE CHO MAY CAT LASER
echo   (Nhan ca file .dxf va anh .png/.jpg)
echo ============================================
echo.

if "%~1"=="" (
    echo Keo tha file ^(hoac thu muc^) vao cua so nay roi nhan Enter.
    set /p "IN=Duong dan: "
) else (
    set "IN=%~1"
)

if not exist "%IN%" (
    echo.
    echo [LOI] Khong tim thay: %IN%
    pause
    exit /b 1
)

echo.
set /p "W=Chieu rong chi tiet, mm - chi can cho anh PNG/JPG [200]: "
if "%W%"=="" set "W=200"

set /p "T=Do day ton, mm [2]: "
if "%T%"=="" set "T=2"

if not exist "%OUT%" mkdir "%OUT%"

rem dxfclean's -o means "output DIRECTORY" when input is a folder, but
rem "output FILE path" when input is a single file -- build the right
rem argument for each case (a classic batch trick: a directory has a NUL
rem pseudo-file inside it, a regular file does not).
set "ISDIR="
if exist "%IN%\NUL" set "ISDIR=1"

if defined ISDIR (
    set "OUTARG=%OUT%"
) else (
    for %%F in ("%IN%") do set "NAME=%%~nF"
    set "OUTARG=%OUT%\!NAME!.dxf"
)

echo.
echo Dang xu ly...
echo.
python -m dxf_cleaner.cli "%IN%" -o "!OUTARG!" -w %W% -t %T%

if errorlevel 1 (
    echo.
    echo [LOI] Chay that bai, hoac co file bi "critical" ^(xem chi tiet o tren^).
    echo Neu bao thieu thu vien / "No module named dxf_cleaner", chay CAI-DAT.bat truoc.
) else (
    echo.
    echo Ket qua nam trong thu muc: %OUT%
    echo   - file .dxf              : dua vao CypCut
    echo   - file *_KIEMTRA.png     : anh kiem tra bat buoc phai xem qua truoc khi cat
    echo   - BAO-CAO.csv            : bang tong hop ^(chi co khi xu ly ca thu muc^)
    start "" "%OUT%"
)

echo.
pause
