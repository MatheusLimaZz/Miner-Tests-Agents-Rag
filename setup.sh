#!/usr/bin/env bash
set -e

echo "======================================================"
echo "          MSR-Kit: Mineração de Literatura Cinza"
echo "======================================================"
echo ""

# 1. Verificar se Python 3 esta instalado
if ! command -v python3 &> /dev/null; then
    echo "[ERRO] Python 3 não foi encontrado no seu computador!"
    echo "Por favor, instale o Python 3.11 ou superior."
    exit 1
fi

# 2. Criar ambiente virtual se nao existir
if [ ! -d ".venv" ]; then
    echo "[1/3] Criando ambiente virtual (.venv)..."
    python3 -m venv .venv
else
    echo "[1/3] Ambiente virtual já existe."
fi

# 3. Instalar o MSR-Kit
echo "[2/3] Instalando dependências do MSR-Kit..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

echo "[3/3] Instalação concluída com sucesso!"
echo ""
echo "======================================================"
echo "  Abrindo o Menu Interativo do MSR-Kit..."
echo "======================================================"
echo ""

# 4. Iniciar menu interativo
.venv/bin/msrkit menu
