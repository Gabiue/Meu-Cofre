"""Janela "Importar extrato" (#14): prévia, dúvidas, categoria editável, saldo e confirmação. Offscreen, extratos falsos."""

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import extrato_ofx_falso as falso  # noqa: E402

from meu_cofre import cadastros  # noqa: E402
from meu_cofre import lancamentos as lanc  # noqa: E402
from meu_cofre.ui import tema  # noqa: E402
from meu_cofre.ui.dialogo_contas import DialogoContas  # noqa: E402
from meu_cofre.ui.dialogo_importar_ofx import DialogoImportarOfx  # noqa: E402

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _dialogo(conn):
    d = DialogoImportarOfx(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def _manual(conn, dia, valor, conta="Inter"):
    return lanc.registrar(conn, entrada=False, valor_centavos=valor, descricao="À mão", categoria_id=None, metodo="pix",
                          conta_id=id_conta(conn, conta), data=dia)[0]


def test_previa_escolhe_a_conta_pelo_banco_e_so_grava_ao_confirmar(app, conn, tmp_path):
    d = _dialogo(conn)
    assert not d.botao_importar.isEnabled() and not d.marcador_saldo.isVisible()
    assert d.carregar(falso.caixa(tmp_path / "c.ofx"))
    assert d.conta.currentText() == "Caixa"
    assert d.carregar(falso.inter(tmp_path / "i.ofx")) and d.conta.currentText() == "Inter"
    assert d.pilula_novas.text() == "▲ 4 novas" and d.pilula_ja.text() == "0 já importadas" and d.pilula_duvidas.text() == "0 dúvidas"
    assert d.botao_importar.text() == "Importar 4" and d.marcador_saldo.isChecked()
    assert d.marcador_saldo.text() == "Acertar o saldo pelo extrato (R$ 2.304,10 em 10/10)"
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    avisos = []
    d.gravado.connect(avisos.append)
    assert d.confirmar()
    assert avisos == ["4 lançamentos importados · saldo acertado (R$ 1.000,00 no saldo inicial)"]
    assert cadastros.saldo_da_conta(conn, id_conta(conn, "Inter"), date(2026, 10, 10)) == 230_410


def test_categoria_sugerida_e_editavel_e_marcador_desligado_nao_acerta_saldo(app, conn, tmp_path):
    lanc.registrar(conn, entrada=False, valor_centavos=500, descricao="Café Modelo", categoria_id=id_cat(conn, "Alimentação fora"),
                   metodo="debito", conta_id=id_conta(conn, "Inter"), data=date(2026, 9, 1))
    d = _dialogo(conn)
    d.carregar(falso.inter(tmp_path / "i.ofx"))
    cafe = next(l for l in d.linhas_novas if l.item.transacao.descricao == "Café Modelo")
    assert cafe.categoria.currentText() == "Alimentação fora"
    pix = next(l for l in d.linhas_novas if l.item.transacao.descricao.startswith("Pix enviado"))
    assert pix.categoria.currentText() == "A categorizar"
    mercado = next(l for l in d.linhas_novas if "Mercado" in l.item.transacao.descricao)
    mercado.categoria.setCurrentIndex(mercado.categoria.findText("Mercado"))
    d.marcador_saldo.setChecked(False)
    d.confirmar()
    assert conn.execute("SELECT categoria_id FROM lancamentos WHERE descricao LIKE '%Mercado%'").fetchone()[0] == id_cat(conn, "Mercado")
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE nome = 'Inter'").fetchone()[0] == 0


def test_duvida_com_escolha_e_conciliada(app, conn, tmp_path):
    _manual(conn, date(2026, 10, 3), 4590)  # concilia o Mercado
    a, b = _manual(conn, date(2026, 10, 8), 3000), _manual(conn, date(2026, 10, 10), 3000)  # dúvida no Café
    d = _dialogo(conn)
    d.carregar(falso.inter(tmp_path / "i.ofx"))
    assert d.pilula_conciliadas.text() == "✓ 1 conciliadas" and d.pilula_duvidas.text() == "! 1 dúvidas"
    assert d.botao_importar.text() == "Importar 4" and d.previa.a_gravar == 2  # 2 novas + 1 conciliada + a dúvida
    escolha = d.linhas_duvidas[0].escolha
    assert escolha.count() == 3 and escolha.itemText(2) == "Importar como nova"
    escolha.setCurrentIndex(2)  # importar como nova
    assert d.previa.a_gravar == 3
    escolha.setCurrentIndex(escolha.findData(b))
    d.confirmar()
    assert conn.execute("SELECT conferido FROM lancamentos WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT conferido FROM lancamentos WHERE id = ?", (a,)).fetchone()[0] == 0


def test_reimportar_so_oferece_acertar_o_saldo_e_erro_fica_na_janela(app, conn, tmp_path):
    arquivo = falso.inter(tmp_path / "i.ofx")
    d = _dialogo(conn)
    d.carregar(arquivo)
    d.marcador_saldo.setChecked(False)
    d.confirmar()
    d2 = _dialogo(conn)
    d2.carregar(arquivo)
    assert d2.pilula_ja.text() == "4 já importadas" and d2.pilula_novas.text() == "0 novas"
    assert d2.botao_importar.text() == "Acertar o saldo" and d2.detalhe_saldo.text() == "O saldo inicial da conta muda R$ 1.000,00."
    d2.marcador_saldo.setChecked(False)
    assert not d2.botao_importar.isEnabled() and not d2.confirmar()
    ruim = tmp_path / "x.ofx"
    ruim.write_text("não é ofx", encoding="utf-8")
    assert not d2.carregar(ruim) and "OFX" in d2.erro.text() and not d2.botao_importar.isEnabled()


def test_botao_importar_extrato_na_janela_de_contas_atualiza_os_saldos(app, conn, tmp_path):
    contas = DialogoContas(conn, hoje=HOJE)
    contas.show()
    avisos = []
    contas.alterado.connect(avisos.append)
    d = contas.importar_extrato(str(falso.inter(tmp_path / "i.ofx")))
    QApplication.processEvents()
    assert contas.botao_importar.text() == "Importar extrato" and d.botao_importar.text() == "Importar 4"
    d.confirmar()
    assert len(avisos) == 1 and contas._saldos[id_conta(conn, "Inter")].text() == "1.000,00"
