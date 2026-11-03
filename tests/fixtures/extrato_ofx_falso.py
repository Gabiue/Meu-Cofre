"""Extratos OFX FALSOS (nomes, valores e descrições inventados) que imitam os dois formatos reais:

- `inter`: OFX 1.02 SGML do banco; o cabeçalho diz USASCII/1252 mas o conteúdo é UTF-8; CRLF; tipos CREDIT e PAYMENT;
  FITID numérico de verdade; descrição em NAME e MEMO; datas com hora e fuso.
- `caixa`: OFX convertido de PDF; UTF-8, LF; CREDIT/DEBIT; FITID inventado (data + sequencial, igual ao CHECKNUM);
  só MEMO, que começa com o horário.
"""

CABECALHO_INTER = (
    "OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\nENCODING:USASCII\r\nCHARSET:1252\r\n"
    "COMPRESSION:NONE\r\nOLDFILEUID:NONE\r\nNEWFILEUID:NONE\r\n\r\n"
)
CABECALHO_CAIXA = CABECALHO_INTER.replace("\r\n", "\n").replace("USASCII", "UTF-8").replace("1252", "NONE")


def _ofx(cabecalho, org, fid, transacoes, saldo, nl):
    corpo = [
        "<OFX>", "<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>",
        f"<FI><ORG>{org}</ORG><FID>{fid}</FID></FI></SONRS></SIGNONMSGSRSV1>",
        "<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL</CURDEF>",
        f"<BANKACCTFROM><BANKID>{fid}</BANKID><ACCTID>0000000</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>",
        "<BANKTRANLIST>", "<DTSTART>20261001</DTSTART>", "<DTEND>20261010</DTEND>",
    ]
    for t in transacoes:
        corpo.append("<STMTTRN>")
        corpo += [f"<{k}>{v}</{k}>" for k, v in t.items()]
        corpo.append("</STMTTRN>")
    corpo += [
        "</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{saldo}</BALAMT><DTASOF>20261010</DTASOF></LEDGERBAL>",
        "</STMTRS></STMTTRNRS></BANKMSGSRSV1>", "</OFX>",
    ]
    return (cabecalho + nl.join(corpo) + nl).encode("utf-8")


def _t(tipo, data, valor, fitid, **extra):
    return {"TRNTYPE": tipo, "DTPOSTED": data, "TRNAMT": valor, "FITID": fitid, **extra, "CHECKNUM": extra.get("CHECKNUM", "077")}


def inter(caminho):
    transacoes = [
        _t("CREDIT", "20261001", "1500.00", "480000000011", NAME="Empresa Ficticia Ltda", MEMO='Pix recebido: "Empresa Ficticia Ltda"'),
        _t("PAYMENT", "20261003120000[-3:BRT]", "-45.90", "480000000022", NAME="Mercado Exemplo", MEMO="Compra no debito: Mercado Exemplo"),
        _t("PAYMENT", "20261005", "-120.00", "480000000033", NAME="Fulano de Tal", MEMO='Pix enviado: "Fulano de Tal"'),
        _t("PAYMENT", "20261009", "-30.00", "480000000044", NAME="Café Modelo", MEMO="Café Modelo"),
    ]
    caminho.write_bytes(_ofx(CABECALHO_INTER, "Banco Ficticio S/A", "077", transacoes, "2304.10", "\r\n"))
    return caminho


def caixa(caminho):
    def t(tipo, data, valor, seq, memo):
        fitid = f"{data}{seq:02d}"
        return {"TRNTYPE": tipo, "DTPOSTED": data, "TRNAMT": valor, "FITID": fitid, "CHECKNUM": fitid, "MEMO": memo}

    transacoes = [
        t("CREDIT", "20261001", "2500.00", 1, "- 08:00:00 CREDITO SALARIO FICTICIO"),
        t("DEBIT", "20261002", "-10.00", 1, "- 08:15:00 COMPRA DEBITO CAFE EXEMPLO"),
        t("DEBIT", "20261002", "-10.00", 2, "- 12:40:00 COMPRA DEBITO CAFE EXEMPLO"),
        t("DEBIT", "20261004", "-80.50", 1, "- 19:02:10 PIX ENVIADO CONTA PROPRIA"),
    ]
    caminho.write_bytes(_ofx(CABECALHO_CAIXA, "Caixa Economica Ficticia", "104", transacoes, "2399.50", "\n"))
    return caminho
