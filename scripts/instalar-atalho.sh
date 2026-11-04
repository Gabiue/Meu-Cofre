#!/bin/sh
# Instala (ou atualiza) o meu-cofre no lançador do sistema.
#
# O atalho roda uma cópia estável da `main` (um git worktree separado), não a pasta de desenvolvimento:
# trocar de branch aqui nunca muda o app aberto pelo lançador. Rode de novo depois de cada merge.
#
#   sh scripts/instalar-atalho.sh
#
# Variáveis opcionais: MEU_COFRE_ESTAVEL (pasta da cópia) e MEU_COFRE_DB (banco usado pelo atalho).
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
ESTAVEL=${MEU_COFRE_ESTAVEL:-$HOME/.local/share/meu-cofre/estavel}
BANCO=${MEU_COFRE_DB:-$REPO/data/meu-cofre.db}
UV=$(command -v uv)

git -C "$REPO" fetch -q origin main 2>/dev/null || echo "aviso: sem acesso ao GitHub, usando a última main baixada"
if [ -e "$ESTAVEL/.git" ]; then
    git -C "$ESTAVEL" checkout -q --detach origin/main
else
    mkdir -p "$(dirname "$ESTAVEL")"
    git -C "$REPO" worktree add -q --detach "$ESTAVEL" origin/main
fi

ICONES=$HOME/.local/share/icons/hicolor/scalable/apps
APLICATIVOS=$HOME/.local/share/applications
mkdir -p "$ICONES" "$APLICATIVOS"
cp "$ESTAVEL/app/meu_cofre/ui/icone.svg" "$ICONES/meu-cofre.svg"

# O banco fica na pasta do projeto (data/ não existe na cópia): o atalho aponta pra ele.
cat > "$APLICATIVOS/meu-cofre.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=meu-cofre
GenericName=Finanças pessoais
Comment=Controle financeiro local: seus dados nunca saem do PC
Exec=env MEU_COFRE_DB=$BANCO $UV run meu-cofre
Path=$ESTAVEL
Icon=meu-cofre
Terminal=false
Categories=Office;Finance;
Keywords=financas;dinheiro;gastos;cofre;orçamento;
StartupWMClass=meu-cofre
EOF

update-desktop-database -q "$APLICATIVOS" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "meu-cofre instalado: $(git -C "$ESTAVEL" log -1 --format='%h %s')"
