@echo off
chcp 65001 >nul
title Dashboard de Produtividade
cd /d "%~dp0"

echo.
echo ============================================
echo   Dashboard de Produtividade
echo ============================================
echo.

if exist "venv\Scripts\activate.bat" (
    echo [OK] Ativando ambiente virtual...
    call "venv\Scripts\activate.bat"
) else (
    echo [AVISO] venv\ nao encontrado — usando Python do sistema.
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    pause
    exit /b 1
)

if not exist "tracker.db" (
    echo [AVISO] tracker.db nao encontrado.
    echo         Rode o tracker.py primeiro para gerar dados.
    echo.
    pause
    exit /b 1
)

echo [OK] Gerando dashboard...
echo.

python dashboard.py

echo.
echo Concluido.
pause