@echo off
chcp 65001 >nul
title dxfclean - Giao dien

python -m dxf_cleaner.gui
if errorlevel 1 (
    echo.
    echo [LOI] Khong mo duoc giao dien.
    echo Neu bao thieu thu vien / "No module named dxf_cleaner", chay CAI-DAT.bat truoc.
    pause
)
