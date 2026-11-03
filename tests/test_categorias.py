"""Categorias (issue #2): regras em cadastros e a janela "Categorias". Offscreen."""

from datetime import date

import pytest
from conftest import id_cat
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from meu_cofre import cadastros
from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.dialogo_categorias import DialogoCategorias
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


@pytest.fixture
def mudancas():
    return []


def _natureza(conn, nome):
    return conn.execute("SELECT natureza FROM categorias WHERE nome = ?", (nome,)).fetchone()[0]


def _janela(conn):
    d = DialogoCategorias(conn)
    d.show()
    QApplication.processEvents()
    return d


def _textos(widget):
    return [l.text() for l in widget.findChildren(QLabel)]


# --- Regras ----------------------------------------------------------------------------


def test_criar_categoria(conn):
    despesa = cadastros.criar_categoria(conn, "  Pets ", "despesa", "flexivel")
    receita = cadastros.criar_categoria(conn, "Freelance", "receita")
    linhas = {r["id"]: r for r in conn.execute("SELECT * FROM categorias")}
    assert (linhas[despesa]["nome"], linhas[despesa]["natureza"], linhas[despesa]["ativa"]) == ("Pets", "flexivel", 1)
    assert (linhas[receita]["tipo"], linhas[receita]["natureza"]) == ("receita", None)


@pytest.mark.parametrize(
    "nome, tipo, natureza, erro",
    [
        ("  ", "despesa", None, "Diga o nome"),
        ("lazer", "despesa", None, "Já existe"),  # sem diferenciar maiúsculas
        ("Nova", "transferencia", None, "despesa ou receita"),
        ("Nova", "receita", "essencial", "Só despesa"),
        ("Nova", "despesa", "caro", "Natureza inválida"),
    ],
)
def test_criar_categoria_invalida(conn, nome, tipo, natureza, erro):
    with pytest.raises(ValueError, match=erro):
        cadastros.criar_categoria(conn, nome, tipo, natureza)


def test_renomear_e_natureza(conn):
    lazer = id_cat(conn, "Lazer")
    cadastros.renomear_categoria(conn, lazer, "Diversão")
    cadastros.renomear_categoria(conn, lazer, "diversão")  # mesmo nome, outra caixa: é a própria categoria
    with pytest.raises(ValueError, match="Já existe"):
        cadastros.renomear_categoria(conn, lazer, "MERCADO")
    cadastros.definir_natureza(conn, lazer, "superfluo")
    assert _natureza(conn, "diversão") == "superfluo"
    cadastros.definir_natureza(conn, lazer, None)
    assert _natureza(conn, "diversão") is None
    with pytest.raises(ValueError, match="Só despesa"):
        cadastros.definir_natureza(conn, id_cat(conn, "Salário"), "essencial")


def test_desativar_preserva_historico_e_some_das_escolhas(conn):
    lazer = id_cat(conn, "Lazer")
    conta = conn.execute("SELECT id FROM contas WHERE nome = 'Inter'").fetchone()[0]
    lanc.registrar(conn, entrada=False, valor_centavos=5000, descricao="Cinema", categoria_id=lazer,
                   metodo="pix", conta_id=conta, data=HOJE)
    cadastros.desativar_categoria(conn, lazer)
    assert "Lazer" not in [c["nome"] for c in lanc.listar_categorias(conn, "despesa")]
    assert conn.execute("SELECT categoria_id FROM lancamentos").fetchone()[0] == lazer
    cadastros.reativar_categoria(conn, lazer)
    assert "Lazer" in [c["nome"] for c in lanc.listar_categorias(conn, "despesa")]


@pytest.mark.parametrize("nome", ["A categorizar", "Pagamento de fatura", "Guardar em meta", "Transferência entre contas"])
def test_categorias_do_sistema_sao_intocaveis(conn, nome):
    id_ = id_cat(conn, nome)
    for acao in (
        lambda: cadastros.renomear_categoria(conn, id_, "Outro nome"),
        lambda: cadastros.desativar_categoria(conn, id_),
        lambda: cadastros.definir_natureza(conn, id_, None),
    ):
        with pytest.raises(ValueError, match="do sistema"):
            acao()


def test_todas_as_do_seed_podem_ser_ajustadas(conn):
    for c in cadastros.categorias_por_tipo(conn, "despesa") + cadastros.categorias_por_tipo(conn, "receita"):
        if cadastros.categoria_do_sistema(c):
            continue
        cadastros.renomear_categoria(conn, c["id"], c["nome"] + " 2")
        cadastros.desativar_categoria(conn, c["id"])
        cadastros.reativar_categoria(conn, c["id"])


# --- Janela ----------------------------------------------------------------------------


def test_lista_por_tipo_com_natureza(app, conn):
    d = _janela(conn)
    textos = _textos(d)
    assert "DESPESAS" in textos and "RECEITAS" in textos and "Salário" in textos
    assert "Transferência entre contas" not in textos  # transferência não é escolha do dono
    assert "do sistema" in "".join(textos)  # A categorizar
    assert d.isModal() and not d.botao_concluir.isDefault()


def test_mudar_natureza_grava_e_avisa(app, conn, mudancas):
    d = _janela(conn)
    d.alterado.connect(mudancas.append)
    d.definir_natureza(id_cat(conn, "Delivery"), "essencial")
    assert _natureza(conn, "Delivery") == "essencial"
    assert mudancas == ["Natureza de Delivery: essencial"]


def test_criar_pela_janela(app, conn, mudancas):
    d = _janela(conn)
    d.alterado.connect(mudancas.append)
    d.iniciar_adicao("despesa")
    assert d._formularios["despesa"].isVisible() and not d._formularios["receita"].isVisible()
    assert not d.adicionar_categoria("despesa") and "Diga o nome" in d.erro_novo["despesa"].text()
    d.novo_nome["despesa"].setText("lazer")
    assert not d.adicionar_categoria("despesa") and "Já existe" in d.erro_novo["despesa"].text()
    d.novo_nome["despesa"].setText("Pets")
    assert d.erro_novo["despesa"].text() == ""  # digitar limpa o erro
    d.nova_natureza.setCurrentIndex(d.nova_natureza.findData("essencial"))
    assert d.adicionar_categoria("despesa")
    assert _natureza(conn, "Pets") == "essencial" and not d._formularios["despesa"].isVisible()
    d.iniciar_adicao("receita")
    d.novo_nome["receita"].setText("Freelance")
    assert d.adicionar_categoria("receita")
    assert conn.execute("SELECT tipo FROM categorias WHERE nome = 'Freelance'").fetchone()[0] == "receita"
    assert mudancas == ["Categoria criada: Pets", "Categoria criada: Freelance"]


def test_renomear_pela_janela(app, conn, mudancas):
    lazer = id_cat(conn, "Lazer")
    d = _janela(conn)
    d.alterado.connect(mudancas.append)
    d.pedir_renomear(lazer)
    d.campo_renomear.setText("Mercado")
    assert not d.salvar_renome() and "Já existe" in d.erro_renomear.text()
    d.campo_renomear.setText("Diversão")
    assert d.erro_renomear.text() == ""
    assert d.salvar_renome()
    assert "Diversão" in _textos(d) and mudancas == ["Categoria renomeada: Diversão"]


def test_desativar_com_confirmacao_e_reativar(app, conn, mudancas):
    delivery = id_cat(conn, "Delivery")
    d = _janela(conn)
    d.alterado.connect(mudancas.append)
    d.pedir_desativar(delivery)
    assert any("Desativar Delivery?" in t for t in _textos(d))
    d.botao_nao.click()
    assert conn.execute("SELECT ativa FROM categorias WHERE id = ?", (delivery,)).fetchone()[0] == 1 and mudancas == []
    d.pedir_desativar(delivery)
    d.botao_sim.click()
    assert conn.execute("SELECT ativa FROM categorias WHERE id = ?", (delivery,)).fetchone()[0] == 0
    assert "Delivery" not in _textos(d) and d._botoes_desativadas["despesa"].isVisible()  # recolhida
    d.mostrar_desativadas("despesa", True)
    assert "Delivery" in _textos(d) and "desativada" in "".join(_textos(d))
    d.reativar(delivery)
    assert conn.execute("SELECT ativa FROM categorias WHERE id = ?", (delivery,)).fetchone()[0] == 1
    assert mudancas == ["Categoria desativada: Delivery", "Categoria reativada: Delivery"]


def test_botao_na_janela_principal_atualiza_e_o_lancamento_reflete(app, conn):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.show()
    assert j.botao_categorias.text() == "Categorias"
    j.botao_categorias.click()
    QApplication.processEvents()
    d = j.dialogo_categorias
    assert isinstance(d, DialogoCategorias)
    d.iniciar_adicao("despesa")
    d.novo_nome["despesa"].setText("Pets")
    assert d.adicionar_categoria("despesa")
    assert "Categoria criada: Pets" in j.statusBar().currentMessage()
    d.pedir_desativar(id_cat(conn, "Lazer"))
    d.confirmar_desativar()
    d.accept()
    QApplication.processEvents()
    assert j.dialogo_categorias is None
    lancamento = DialogoLancamento(conn, hoje=HOJE)
    nomes = [lancamento.categoria.itemText(i) for i in range(lancamento.categoria.count())]
    assert "Pets" in nomes and "Lazer" not in nomes


@pytest.mark.parametrize("largura", [760, 940, 1400])
def test_cabecalho_comporta_os_tres_botoes(app, conn, largura):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(largura, 900)
    j.show()
    QApplication.processEvents()
    for b in (j.botao_contas, j.botao_categorias, j.botao_novo):
        assert b.isVisible() and 0 <= b.mapTo(j, b.rect().topLeft()).x() and b.mapTo(j, b.rect().bottomRight()).x() <= largura
