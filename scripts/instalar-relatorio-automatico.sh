#!/bin/sh
# Instala (ou remove) o timer do systemd que gera o relatório do mês passado no dia 1 de cada mês.
#
#   sh scripts/instalar-relatorio-automatico.sh            # instala/atualiza e liga o timer
#   sh scripts/instalar-relatorio-automatico.sh --remover  # desliga e apaga os arquivos
#
# Roda `meu-cofre relatorio` na cópia estável criada por scripts/instalar-atalho.sh (rode aquele antes).
# Passo opcional depois de gerar: se existir um executável ~/.config/meu-cofre/depois-do-relatorio (você cria,
# ex.: mandar por email), ele é chamado com o caminho do arquivo como argumento. O app em si nunca envia nada.
# Variáveis opcionais: MEU_COFRE_ESTAVEL, MEU_COFRE_DB e MEU_COFRE_SEM_SYSTEMCTL=1 (só escreve os arquivos; usado nos testes).
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
ESTAVEL=${MEU_COFRE_ESTAVEL:-$HOME/.local/share/meu-cofre/estavel}
BANCO=${MEU_COFRE_DB:-$REPO/data/meu-cofre.db}
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
UNIDADES=$CONFIG/systemd/user
GANCHO=$CONFIG/meu-cofre/depois-do-relatorio
SERVICO=$UNIDADES/meu-cofre-relatorio.service
TIMER=$UNIDADES/meu-cofre-relatorio.timer
WRAPPER=$UNIDADES/meu-cofre-relatorio.sh

systemctl_usuario() {
    [ "${MEU_COFRE_SEM_SYSTEMCTL:-}" = 1 ] || systemctl --user "$@"
}

if [ "${1:-}" = "--remover" ]; then
    systemctl_usuario disable --now meu-cofre-relatorio.timer 2>/dev/null || true
    rm -f "$SERVICO" "$TIMER" "$WRAPPER"
    systemctl_usuario daemon-reload
    echo "timer do relatório removido"
    exit 0
fi

if [ ! -d "$ESTAVEL" ]; then
    echo "erro: cópia estável não encontrada em $ESTAVEL; rode antes: sh scripts/instalar-atalho.sh" >&2
    exit 1
fi
UV=$(command -v uv)
mkdir -p "$UNIDADES"

# O wrapper gera o relatório e, se o gancho existir, entrega o caminho do arquivo a ele.
cat > "$WRAPPER" <<EOF2
#!/bin/sh
set -eu
arquivo=\$(env MEU_COFRE_DB="$BANCO" "$UV" run meu-cofre relatorio)
if [ -n "\$arquivo" ] && [ -x "$GANCHO" ]; then
    "$GANCHO" "\$arquivo"
fi
EOF2
chmod +x "$WRAPPER"

cat > "$SERVICO" <<EOF2
[Unit]
Description=meu-cofre: relatório do mês passado

[Service]
Type=oneshot
WorkingDirectory=$ESTAVEL
ExecStart=/bin/sh $WRAPPER
EOF2

cat > "$TIMER" <<EOF2
[Unit]
Description=meu-cofre: relatório no dia 1 de cada mês

[Timer]
OnCalendar=*-*-01 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF2

systemctl_usuario daemon-reload
systemctl_usuario enable --now meu-cofre-relatorio.timer
echo "timer instalado: relatório do mês passado todo dia 1, às 8h (ou no próximo boot, se o PC estava desligado)"
