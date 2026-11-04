# meu-cofre

Ferramenta local de finanças pessoais: lance gastos em poucos segundos, veja pra onde o dinheiro foi e como o mês vai terminar. App desktop feito com Python, SQLite, pandas e PySide6 (Qt).

> **Em construção.** A tela inicial ainda usa dados de exemplo; importação de extratos, recorrentes e conciliação vêm depois.

## Privacidade

Tudo roda no seu computador. Os dados ficam num arquivo SQLite em `data/meu-cofre.db`, e a pasta `data/` está fora do git (veja o `.gitignore`). Nada é enviado pra internet. Nunca coloque dados financeiros reais no repositório nem nos testes.

Pra usar outro arquivo de banco, defina a variável `MEU_COFRE_DB`:

    MEU_COFRE_DB=/caminho/teste.db uv run meu-cofre

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) e Python 3.12 ou mais novo.

    uv sync
    uv run meu-cofre

Dados de demonstração (falsos), num banco separado:

    uv run python scripts/dados_demo.py
    MEU_COFRE_DB=data/demo.db uv run meu-cofre

## Atalho no menu do sistema (Linux)

    sh scripts/instalar-atalho.sh

Cria o atalho (`.desktop`) e o ícone. O atalho roda uma cópia estável da `main` em `~/.local/share/meu-cofre/estavel`, então trocar de branch na pasta do projeto não muda o app aberto pelo menu; ele usa o banco em `data/meu-cofre.db` do projeto. Rode o mesmo comando depois de cada merge pra atualizar.

## Testes

    uv run pytest -q

Os testes usam bancos temporários e só dados falsos.

## Estrutura

    app/meu_cofre/
      config.py       caminho do banco
      db.py           conexão, criação do schema e seed
      schema.sql      tabelas
      seed.py         contas e categorias iniciais
      lancamentos.py  registrar, listar, editar, sugerir categoria
      relatorios.py   números da tela inicial
      cadastros.py    salvar categorias e contas
      ui/             interface PySide6 (só chama o pacote)
        app.py          comando `meu-cofre`
        tema.py         cores, fontes e estilo
        componentes.py  cartões, botões, pílulas, itens de lista
        janela.py       tela inicial
    scripts/          dados de demonstração
    tests/            testes com pytest
    data/             banco local (fora do git)
