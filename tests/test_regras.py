"""Regras de categorização (issue #3): lógica, importação OFX, fila "A categorizar" e telas. Offscreen, dados falsos."""

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import extrato_ofx_falso as falso  # noqa: E402

from meu_cofre import cadastros, importar_ofx  # noqa: E402
from meu_cofre import lancamentos as lanc  # noqa: E402
from meu_cofre.ui import tema  # noqa: E402
from meu_cofre.ui.dialogo_a_categorizar import DialogoACategorizar  # noqa: E402
from meu_cofre.ui.dialogo_categorias import DialogoCategorias  # noqa: E402
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento  # noqa: E402
from meu_cofre.ui.janela import JanelaPrincipal  # noqa: E402

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _reg(conn, descricao, cat=None, *, entrada=False, dia=5, valor=1000):
    return lanc.registrar(conn, entrada=entrada, valor_centavos=valor, descricao=descricao,
                          categoria_id=id_cat(conn, cat) if cat else None, metodo="pix",
                          conta_id=id_conta(conn, "Inter"), data=date(2026, 10, dia))[0]


def _categoria_de(conn, id_):
    return conn.execute("SELECT categoria_id FROM lancamentos WHERE id = ?", (id_,)).fetchone()[0]


# --- Lógica ---------------------------------------------------------------------------


def test_criar_editar_excluir_e_ordem_por_prioridade(conn):
    uber = cadastros.criar_regra(conn, "  uber ", id_cat(conn, "Transporte"), prioridade=50)
    cadastros.criar_regra(conn, "ifood", id_cat(conn, "Delivery"), prioridade=10)
    assert [r["padrao"] for r in cadastros.listar_regras(conn)] == ["ifood", "uber"]
    cadastros.editar_regra(conn, uber, "uber trip", id_cat(conn, "Transporte"), "regex", 1)
    primeira = cadastros.listar_regras(conn)[0]
    assert (primeira["padrao"], primeira["tipo_match"], primeira["categoria"]) == ("uber trip", "regex", "Transporte")
    cadastros.excluir_regra(conn, uber)
    assert len(cadastros.listar_regras(conn)) == 1


@pytest.mark.parametrize(
    "padrao, tipo, cat, erro",
    [
        ("   ", "contem", "Lazer", "Diga o trecho"),
        ("!!!", "contem", "Lazer", "letras ou números"),
        ("(abc", "regex", "Lazer", "Regex inválida"),
        ("x", "glob", "Lazer", "contém ou regex"),
        ("x", "contem", "A categorizar", "categoria ativa"),
        ("x", "contem", "Transferência entre contas", "categoria ativa"),
    ],
)
def test_regra_invalida(conn, padrao, tipo, cat, erro):
    with pytest.raises(ValueError, match=erro):
        cadastros.criar_regra(conn, padrao, id_cat(conn, cat), tipo)
    assert cadastros.listar_regras(conn) == []


def test_regra_de_categoria_desativada_e_recusada(conn):
    cadastros.desativar_categoria(conn, id_cat(conn, "Lazer"))
    with pytest.raises(ValueError, match="categoria ativa"):
        cadastros.criar_regra(conn, "cinema", id_cat(conn, "Lazer"))


def test_pronto_quando_regra_categoriza_o_proximo_lancamento(conn):
    assert lanc.sugerir_categoria(conn, "Uber *trip 123", "despesa") is None
    cadastros.criar_regra(conn, "uber", id_cat(conn, "Transporte"))
    assert lanc.sugerir_categoria(conn, "Uber *trip 123", "despesa") == id_cat(conn, "Transporte")


def test_menor_prioridade_vence_e_regra_vence_historico(conn):
    cadastros.criar_regra(conn, "ifood", id_cat(conn, "Lazer"), prioridade=100)
    cadastros.criar_regra(conn, "ifood", id_cat(conn, "Delivery"), prioridade=5)
    _reg(conn, "iFood pedido", "Mercado")  # histórico diz Mercado; a regra manda
    assert lanc.sugerir_categoria(conn, "iFood pedido", "despesa") == id_cat(conn, "Delivery")


def test_regex_e_tipo_da_categoria(conn):
    cadastros.criar_regra(conn, r"^sal[aá]rio \d+$", id_cat(conn, "Salário"), "regex")
    assert lanc.sugerir_categoria(conn, "Salário 10", "receita") == id_cat(conn, "Salário")
    assert lanc.sugerir_categoria(conn, "Salário 10", "despesa") is None  # categoria de receita não serve a uma saída


def test_pendentes_e_aplicar_regras_so_nos_sem_categoria(conn):
    a = _reg(conn, "Uber centro", dia=3)
    b = _reg(conn, "Uber aeroporto", "Lazer", dia=4)  # já categorizado: intocável
    c = _reg(conn, "Uber casa", dia=6)
    d = _reg(conn, "Padaria", dia=7)
    e = lanc.registrar(conn, entrada=False, valor_centavos=1000, descricao="Uber mesma coisa",
                       categoria_id=id_cat(conn, "A categorizar"), metodo="pix", conta_id=id_conta(conn, "Inter"), data=HOJE)[0]
    lanc.transferir(conn, origem_id=id_conta(conn, "Inter"), destino_id=id_conta(conn, "Carteira"), valor_centavos=500,
                    data=HOJE, descricao="Uber transferência")
    assert lanc.pendentes(conn)["id"].tolist() == [e, d, c, a]  # mais recente primeiro; sem a transferência
    cadastros.criar_regra(conn, "uber", id_cat(conn, "Transporte"))
    assert lanc.contar_pendentes_da_regra(conn) == 3
    assert lanc.aplicar_regras_aos_pendentes(conn) == 3
    assert [_categoria_de(conn, i) for i in (a, b, c, d, e)] == [
        id_cat(conn, "Transporte"), id_cat(conn, "Lazer"), id_cat(conn, "Transporte"), None, id_cat(conn, "Transporte")]
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'Uber transferência' AND categoria_id = ?",
                        (id_cat(conn, "Transporte"),)).fetchone()[0] == 0
    assert lanc.aplicar_regras_aos_pendentes(conn) == 0


# --- Importação OFX -------------------------------------------------------------------


def test_ofx_pix_so_e_categorizado_por_regra_nao_pelo_historico(conn, tmp_path):
    _reg(conn, 'Pix enviado: "Fulano de Tal"', "Lazer", dia=1)  # histórico não vale pra Pix
    extrato = importar_ofx.ler(falso.inter(tmp_path / "i.ofx"))
    pix = lambda: next(i for i in importar_ofx.prever(conn, extrato, id_conta(conn, "Inter"), HOJE).novas
                       if i.transacao.descricao.startswith("Pix enviado"))
    assert pix().categoria_id is None
    cadastros.criar_regra(conn, "fulano", id_cat(conn, "Presentes e doações"))
    assert pix().categoria_id == id_cat(conn, "Presentes e doações")


# --- Telas ----------------------------------------------------------------------------


def test_secao_regras_criar_com_erro_inline_oferecer_e_aplicar(app, conn):
    pendente = _reg(conn, "Uber centro")
    d = DialogoCategorias(conn)
    d.show()
    QApplication.processEvents()
    avisos = []
    d.alterado.connect(avisos.append)
    d.iniciar_regra()
    f = d.formulario_regra
    f.padrao.setText("(uber")
    f.tipo.setCurrentIndex(f.tipo.findData("regex"))
    assert not f.salvar() and "Regex inválida" in f.erro.text()
    f.tipo.setCurrentIndex(f.tipo.findData("contem"))
    f.padrao.setText("uber")
    f.categoria.setCurrentIndex(f.categoria.findText("Transporte"))
    assert f.salvar()
    assert [r["padrao"] for r in cadastros.listar_regras(conn)] == ["uber"]
    assert d.texto_oferta.text() == "Aplicar a 1 lançamento sem categoria que casa?"
    assert _categoria_de(conn, pendente) is None
    d.botao_aplicar.click()
    assert _categoria_de(conn, pendente) == id_cat(conn, "Transporte")
    assert avisos[-1] == "1 lançamento categorizado pela regra"
    d.iniciar_regra(cadastros.listar_regras(conn)[0]["id"])
    assert d.formulario_regra.padrao.text() == "uber"
    d.formulario_regra.padrao.setText("uber trip")
    assert d.formulario_regra.salvar()
    assert cadastros.listar_regras(conn)[0]["padrao"] == "uber trip"
    d.excluir_regra(cadastros.listar_regras(conn)[0]["id"])
    assert cadastros.listar_regras(conn) == []


def test_fila_criar_regra_categoriza_a_linha_e_salvar_aplica_o_resto(app, conn):
    a = _reg(conn, "Uber centro", dia=3)
    b = _reg(conn, "Uber casa", dia=4)
    c = _reg(conn, "Padaria Modelo", dia=5)
    e = _reg(conn, "Sem destino", dia=6)
    d = DialogoACategorizar(conn)
    d.show()
    QApplication.processEvents()
    avisos = []
    d.gravado.connect(avisos.append)
    assert d.resumo.text() == "4 lançamentos sem categoria"
    d.combos[a].setCurrentIndex(d.combos[a].findText("Transporte"))
    form = d.criar_regra(a, "Uber centro", d.combos[a].parentWidget())
    assert form.padrao.text() == "uber centro" and form.categoria.currentText() == "Transporte"
    form.padrao.setText("uber")
    d.combos[c].setCurrentIndex(d.combos[c].findText("Mercado"))  # escolha pendente sobrevive à recarga
    assert form.salvar()
    assert _categoria_de(conn, a) == _categoria_de(conn, b) == id_cat(conn, "Transporte")
    assert avisos[-1] == "Regra criada · 2 lançamentos categorizados"
    assert set(d.combos) == {c, e} and d.combos[c].currentText() == "Mercado"
    assert d.salvar()
    assert _categoria_de(conn, c) == id_cat(conn, "Mercado") and _categoria_de(conn, e) is None
    assert set(d.combos) == {e}


def test_pilula_a_categorizar_na_tela_inicial(app, conn):
    janela = JanelaPrincipal(conn, hoje=HOJE)
    janela.show()
    QApplication.processEvents()
    assert not janela.pilula_a_categorizar.isVisible()
    _reg(conn, "Uber centro")
    _reg(conn, "Padaria")
    janela.atualizar()
    assert janela.pilula_a_categorizar.isVisible() and "2 a categorizar" in janela.pilula_a_categorizar.text()
    janela.pilula_a_categorizar.clicada.emit()
    assert isinstance(janela.dialogo_a_categorizar, DialogoACategorizar)
    janela.dialogo_a_categorizar.close()


def test_oferta_de_regra_ao_salvar_e_lancar_outro_com_categoria_diferente_da_sugerida(app, conn):
    d = DialogoLancamento(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    d.valor.setText("20,00")
    d.descricao.setText("Uber *trip 99")
    d.categoria.setCurrentIndex(d.categoria.findText("Transporte"))  # sem sugestão: vale a oferta
    assert d.salvar_e_lancar_outro()
    assert d.oferta_regra.isVisible() and d.padrao_regra.text() == "uber trip"  # sem números nem símbolos
    assert d.texto_oferta.text() == "como Transporte"
    d.padrao_regra.setText("uber")
    assert d.criar_regra() and not d.oferta_regra.isVisible()
    assert lanc.sugerir_categoria(conn, "Uber centro", "despesa") == id_cat(conn, "Transporte")
    # categoria igual à sugerida: nenhuma oferta
    d.valor.setText("10,00")
    d.descricao.setText("Uber centro")
    d.descricao.editingFinished.emit()
    assert d.categoria.currentText() == "Transporte"
    assert d.salvar_e_lancar_outro() and not d.oferta_regra.isVisible()


def test_padrao_sugerido_tira_numeros_e_simbolos():
    from meu_cofre.lancamentos import padrao_sugerido

    assert padrao_sugerido("Uber *trip 8812") == "uber trip"
    assert padrao_sugerido("Pix enviado: Fulano") == "pix enviado fulano"
