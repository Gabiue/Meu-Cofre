#!/bin/sh
# Instalador único do meu-cofre no Linux: cria o atalho no menu (KDE, GNOME, XFCE, rofi...) e o ícone.
#
#   sh scripts/instalar.sh             # instala ou atualiza (rode de novo depois de um `git pull`)
#   sh scripts/instalar.sh --remover   # apaga só o atalho e o ícone
#
# Clone git (com origin e main): o atalho roda uma cópia estável da `main` (git worktree), então trocar
# de branch na pasta do projeto nunca muda o app aberto. Sem git (ZIP): roda a própria pasta do projeto.
# Variáveis opcionais: MEU_COFRE_ESTAVEL, MEU_COFRE_DB e MEU_COFRE_SEM_CACHE=1 (pula os caches do desktop; testes).
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
ESTAVEL=${MEU_COFRE_ESTAVEL:-$HOME/.local/share/meu-cofre/estavel}
DADOS=${XDG_DATA_HOME:-$HOME/.local/share}
ICONES=$DADOS/icons/hicolor/scalable/apps
APLICATIVOS=$DADOS/applications

if [ "${1:-}" = "--remover" ]; then
    rm -f "$APLICATIVOS/meu-cofre.desktop" "$ICONES/meu-cofre.svg"
    echo "atalho e ícone removidos. Seus dados e a cópia estável ficam (apague à mão se quiser):"
    echo "  banco: ${MEU_COFRE_DB:-$DADOS/meu-cofre (ou $REPO/data, se já usava)}"
    echo "  cópia estável: $ESTAVEL"
    exit 0
fi

UV=$(command -v uv) || {
    cat >&2 <<'MSG'
erro: o uv não está instalado. Instale e rode este script de novo:
  https://docs.astral.sh/uv/
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ou pelo gerenciador da distro, ex.: sudo pacman -S uv
MSG
    exit 1
}

# Só conta como clone se a pasta for a raiz do repositório: um ZIP extraído dentro de outro repo não pega o dele.
CLONE=
TOPO=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) && [ "$(cd "$TOPO" && pwd -P)" = "$(cd "$REPO" && pwd -P)" ] && CLONE=1
if [ -n "$CLONE" ] && git -C "$REPO" remote get-url origin >/dev/null 2>&1; then
    git -C "$REPO" fetch -q origin main 2>/dev/null || echo "aviso: sem acesso ao remoto, usando a última main baixada"
fi
if [ -n "$CLONE" ] && git -C "$REPO" show-ref -q --verify refs/remotes/origin/main 2>/dev/null; then
    if [ -e "$ESTAVEL/.git" ]; then
        git -C "$ESTAVEL" checkout -q --detach origin/main
    else
        mkdir -p "$(dirname "$ESTAVEL")"
        git -C "$REPO" worktree add -q --detach "$ESTAVEL" origin/main
    fi
    PASTA=$ESTAVEL
else
    echo "aviso: sem clone git com origin/main; o atalho vai rodar a própria pasta do projeto ($REPO)"
    PASTA=$REPO
fi

(cd "$PASTA" && "$UV" sync --frozen)

mkdir -p "$ICONES" "$APLICATIVOS"
cp "$PASTA/app/meu_cofre/ui/icone.svg" "$ICONES/meu-cofre.svg"

# Só fixa o banco quando veio da variável ou já existe em data/ (instalação antiga); senão a config usa a pasta XDG.
BANCO=${MEU_COFRE_DB:-}
[ -n "$BANCO" ] || { [ ! -e "$REPO/data/meu-cofre.db" ] || BANCO=$REPO/data/meu-cofre.db; }
EXEC="$UV run meu-cofre"
[ -z "$BANCO" ] || EXEC="env MEU_COFRE_DB=$BANCO $EXEC"

cat > "$APLICATIVOS/meu-cofre.desktop" <<EOF2
[Desktop Entry]
Type=Application
Name=meu-cofre
GenericName=Finanças pessoais
Comment=Controle financeiro local: seus dados nunca saem do PC
Exec=$EXEC
Path=$PASTA
Icon=meu-cofre
Terminal=false
Categories=Office;Finance;
Keywords=financas;dinheiro;gastos;cofre;orçamento;
StartupWMClass=meu-cofre
EOF2

if [ "${MEU_COFRE_SEM_CACHE:-}" != 1 ]; then
    update-desktop-database -q "$APLICATIVOS" 2>/dev/null || true
    gtk-update-icon-cache -q "$DADOS/icons/hicolor" 2>/dev/null || true
fi
echo "meu-cofre instalado: atalho em $APLICATIVOS/meu-cofre.desktop"
echo "banco: ${BANCO:-$DADOS/meu-cofre/meu-cofre.db}"
