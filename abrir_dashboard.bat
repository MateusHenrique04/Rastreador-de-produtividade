@echo off
chcp 65001 >nul
title Dashboard de Produtividade
cd /d "%~dp0"

echo.
echo ============================================
echo   Dashboard de Produtividade - ao vivo
echo ============================================
echo.

REM Ativa o venv se existir nesta pasta
if exist "venv\Scripts\activate.bat" (
    echo [OK] Ativando ambiente virtual ^(venv^)...
    call "venv\Scripts\activate.bat"
) else (
    echo [AVISO] Pasta venv\ nao encontrada — usando Python do sistema.
)

REM Verifica se o Python esta acessivel
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo.
    pause
    exit /b 1
)

REM Verifica se o Flask esta disponivel; se nao, instala
python -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Flask nao instalado. Instalando agora...
    echo.
    python -m pip install flask
    if errorlevel 1 (
        echo.
        echo [ERRO] Falha ao instalar Flask.
        pause
        exit /b 1
    )
    echo.
)

REM Verifica se o tracker.db existe
if not exist "tracker.db" (
    echo [AVISO] tracker.db nao encontrado nesta pasta.
    echo         Rode o tracker.py primeiro para gerar dados.
    echo.
    pause
    exit /b 1
)

echo [OK] Iniciando servidor em http://localhost:5000
echo      O navegador vai abrir sozinho em alguns segundos.
echo.
echo      Para parar: feche esta janela ou aperte CTRL+C
echo.

python dashboard_live.py

REM Se o servidor cair, espera o usuario ler antes de fechar
echo.
echo Servidor encerrado.
pause
