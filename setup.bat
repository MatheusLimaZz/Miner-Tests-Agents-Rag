@echo off
setlocal
title MSR-Kit - Setup e Inicializacao

echo ======================================================
echo           MSR-Kit: Mineracao de Literatura Cinza
echo ======================================================
echo.

:: 1. Verificar se Python esta instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] O Python nao foi encontrado no seu computador!
    echo.
    echo 1. Baixe o Python 3.11 ou superior em:
    echo    https://www.python.org/downloads/
    echo.
    echo 2. IMPORTANTE: Durante a instalacao, marque a opcao:
    echo    "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
)

:: 2. Criar ambiente virtual se nao existir
if not exist ".venv" (
    echo [1/3] Criando ambiente virtual isolado (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Ambiente virtual ja existe.
)

:: 3. Instalar o MSR-Kit
echo [2/3] Instalando dependencias do MSR-Kit...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -e .
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar as dependencias.
    pause
    exit /b 1
)

echo [3/3] Instalacao concluida com sucesso!
echo.
echo ======================================================
echo   Abrindo o Menu Interativo do MSR-Kit...
echo ======================================================
echo.

:: 4. Iniciar menu interativo
".venv\Scripts\msrkit.exe" menu

pause
