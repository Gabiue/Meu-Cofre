"""Atualização das taxas do Banco Central em segundo plano (F8): a janela nunca espera a rede.

Só a busca de rede roda numa thread (`QThreadPool`); ler o plano e gravar o resultado acontece na thread principal,
a única que mexe no banco. `MEU_COFRE_SEM_REDE=1` desliga tudo (testes e screenshots). Pra não repetir a busca a cada
lançamento salvo, uma tentativa só acontece a cada `INTERVALO` segundos (a menos que se force).
"""

import sqlite3
import time
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from meu_cofre import indicadores, investimentos

INTERVALO = 20 * 60  # segundos entre tentativas


class _Sinais(QObject):
    pronto = Signal(object, object)  # pontos por série, erros por série


class _Busca(QRunnable):
    """A busca de rede, na thread do pool. Não toca no banco."""

    def __init__(self, plano: list, buscar: Callable | None, sinais: _Sinais):
        super().__init__()
        self._plano, self._buscar, self._sinais = plano, buscar, sinais

    def run(self) -> None:
        pontos, erros = indicadores.buscar_plano(self._plano, self._buscar or indicadores._buscar_http)
        self._sinais.pronto.emit(pontos, erros)


class AtualizadorIndicadores(QObject):
    """`disparar()` começa uma atualização (se couber); `concluido(status)` avisa na thread principal, já com os dados
    gravados (só quando houve busca de rede). `status` é o dict de `indicadores.montar_status`."""

    concluido = Signal(dict)

    def __init__(
        self, conn: sqlite3.Connection, hoje: Callable[[], date], buscar: Callable | None = None, parent: QObject | None = None
    ):
        super().__init__(parent)
        self.conn, self._hoje, self._buscar = conn, hoje, buscar
        self._sinais = _Sinais(self)
        self._sinais.pronto.connect(self._terminou)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._tentou_em: float | None = None
        self.em_andamento = False
        self.status: dict | None = None  # o resultado da última atualização

    def disparar(self, forcar: bool = False) -> bool:
        """Devolve True se saiu uma busca de rede. Não faz nada sem rede ligada, durante outra busca ou logo após uma tentativa."""
        if indicadores.rede_desligada() or self.em_andamento:
            return False
        agora = time.monotonic()
        if not forcar and self._tentou_em is not None and agora - self._tentou_em < INTERVALO:
            return False
        self._tentou_em = agora
        hoje = self._hoje()
        plano = indicadores.plano_de_busca(self.conn, hoje, investimentos.inicio_necessario(self.conn, hoje))
        if not plano:  # tudo em dia: nada a buscar
            self.status = indicadores.montar_status(self.conn, 0, {})
            return False
        self.em_andamento = True
        self._pool.start(_Busca(plano, self._buscar, self._sinais))
        return True

    def _terminou(self, pontos: dict, erros: dict) -> None:
        """Na thread principal: grava o que chegou e avisa."""
        self.em_andamento = False
        self.status = indicadores.montar_status(self.conn, indicadores.gravar(self.conn, pontos), erros)
        self.concluido.emit(self.status)

    def aguardar(self, ms: int = 5000) -> bool:
        """Espera a busca terminar (usado ao fechar o app e nos testes). Entregar o resultado ainda exige o laço de eventos."""
        return self._pool.waitForDone(ms)
