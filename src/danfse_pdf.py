"""Gera a DANFSE (PDF da NFS-e do DF / ISS.NET-Nota Control) a partir dos dados da nota.

Por que existe: o PDF oficial é obtido do ISSnet via `ConsultarUrlNfse`, mas essa
operação dá E160 (schema proprietário indisponível) — ver histórico. Como alternativa
confiável e sem depender do ISSnet, reproduzimos o **mesmo layout** da DANFSE oficial e
preenchemos com os **dados reais** da nota emitida (número, código de verificação, valores,
prestador, tomador, discriminação...). É a prática padrão de ERP (imprimir a DANFSE a partir
dos dados da nota); inclui o link/QR de autenticidade para conferência no portal oficial.

Dependências (Python puro — sem binário/apt na VPS):  reportlab, qrcode, pillow.

Uso:
    from src.danfse_pdf import gerar_danfse_pdf, dados_de_consulta_xml
    dados = dados_de_consulta_xml(xml_consultar_nfse)   # ou montar o dict manualmente
    pdf_bytes = gerar_danfse_pdf(dados)
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Optional

# =============================================================================
# Helpers de formatação
# =============================================================================
def _brl(valor: Any) -> str:
    """Formata número (float/str) como 'R$ 1.234,56'."""
    try:
        v = float(str(valor).replace(".", "").replace(",", ".")) if isinstance(valor, str) and "," in str(valor) else float(valor)
    except (TypeError, ValueError):
        v = 0.0
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _fmt_cnpj(doc: Any) -> str:
    d = re.sub(r"\D", "", str(doc or ""))
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return str(doc or "")


def _fmt_cep(cep: Any) -> str:
    d = re.sub(r"\D", "", str(cep or ""))
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else str(cep or "")


def _fmt_data(iso: Any, com_hora: bool = False) -> str:
    """'2026-08-17T08:34:13' -> '17/08/2026 08:34:13' (ou só data)."""
    s = str(iso or "").strip()
    if not s:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}:\d{2}))?", s)
    if not m:
        return s
    d = f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return f"{d} {m.group(4)}" if (com_hora and m.group(4)) else d


# =============================================================================
# Parser do XML de consulta (ConsultarNfseServicoPrestadoResposta / ...Nfse...)
# =============================================================================
def dados_de_consulta_xml(xml: str) -> dict[str, Any]:
    """Extrai os campos da DANFSE de um XML de NFS-e (ABRASF 2.04, tolerante a namespace).

    Aceita ConsultarNfseServicoPrestadoResposta, ConsultarNfsePorRpsResposta,
    GerarNfseResposta, etc. — busca por localname, ignorando prefixos/NS.
    """
    from lxml import etree

    root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)

    def _t(*nomes):
        """Texto do 1º descendente (em ordem de documento) cujo localname bata."""
        for nome in nomes:
            for e in root.iter():
                if etree.QName(e).localname.lower() == nome.lower() and e.text:
                    return e.text.strip()
        return ""

    def _sub(base, *nomes):
        for nome in nomes:
            for e in base.iter():
                if etree.QName(e).localname.lower() == nome.lower() and e.text:
                    return e.text.strip()
        return ""

    def _first(nome):
        for e in root.iter():
            if etree.QName(e).localname.lower() == nome.lower():
                return e
        return None

    prest = _first("PrestadorServico")
    if prest is None:
        prest = _first("Prestador")
    tom = _first("TomadorServico")
    if tom is None:
        tom = _first("Tomador")
    end_prest = None
    end_tom = None
    if prest is not None:
        for e in prest.iter():
            if etree.QName(e).localname == "Endereco" and len(e):  # o container, não o campo
                end_prest = e
                break
    if tom is not None:
        for e in tom.iter():
            if etree.QName(e).localname == "Endereco" and len(e):
                end_tom = e
                break

    def _endereco(cont):
        if cont is None:
            return {}
        return {
            "logradouro": _sub(cont, "Endereco"),
            "numero": _sub(cont, "Numero"),
            "complemento": _sub(cont, "Complemento"),
            "bairro": _sub(cont, "Bairro"),
            "cep": _sub(cont, "Cep"),
            "uf": _sub(cont, "Uf"),
        }

    return {
        "numero_nfse": _t("Numero"),
        "codigo_verificacao": _t("CodigoVerificacao"),
        "data_geracao": _t("DataEmissao"),  # datetime da NFS-e
        "data_competencia": _t("Competencia"),
        "responsavel_retencao": "Tomador" if _t("ResponsavelRetencao") == "1" else ("Prestador" if _t("ResponsavelRetencao") == "2" else ""),
        # Prestador
        "prest_razao": (_sub(prest, "RazaoSocial") if prest is not None else ""),
        "prest_fantasia": (_sub(prest, "NomeFantasia") if prest is not None else ""),
        "prest_cnpj": _t("Cnpj"),  # 1º Cnpj (ordem doc) = prestador (Rps>Prestador)
        "prest_im": _t("InscricaoMunicipal"),  # 1ª IM (ordem doc) = prestador
        "prest_fone": (_sub(prest, "Telefone") if prest is not None else ""),
        "prest_email": (_sub(prest, "Email") if prest is not None else ""),
        "prest_end": _endereco(end_prest),
        # Identificação RPS
        "natureza_operacao": "Exigível" if _t("ExigibilidadeISS") == "1" else "",
        "numero_rps": _sub(_first("IdentificacaoRps"), "Numero") if _first("IdentificacaoRps") is not None else _t("Numero"),
        "serie_rps": _sub(_first("IdentificacaoRps"), "Serie") if _first("IdentificacaoRps") is not None else "",
        "data_emissao_rps": _fmt_data(_t("Competencia")),
        # Tomador
        "tom_cnpj": (_sub(tom, "Cnpj") if tom is not None else ""),
        "tom_im": (_sub(tom, "InscricaoMunicipal") if tom is not None else ""),
        "tom_razao": (_sub(tom, "RazaoSocial") if tom is not None else ""),
        "tom_email": (_sub(tom, "Email") if tom is not None else ""),
        "tom_fone": (_sub(tom, "Telefone") if tom is not None else ""),
        "tom_end": _endereco(end_tom),
        # Serviço / discriminação
        "descricao": _t("Discriminacao"),
        "cod_trib_mun": _t("CodigoTributacaoMunicipio"),
        "desc_cod_trib_mun": _t("DescricaoCodigoTributacaoMunicípio", "DescricaoCodigoTributacaoMunicipio"),
        "item_lista": _t("ItemListaServico"),
        "cod_cnae": _t("CodigoCnae"),
        "aliquota": _t("Aliquota"),
        # Valores
        "valor_servicos": _t("ValorServicos", "BaseCalculo"),
        "base_calculo": _t("BaseCalculo", "ValorServicos"),
        "valor_iss": _t("ValorIss"),
        "valor_liquido": _t("ValorLiquidoNfse"),
        "iss_retido": "Sim" if _t("IssRetido") == "1" else "Não",
        # Extras
        "outras_informacoes": _t("OutrasInformacoes"),
        "optante_simples": _t("OptanteSimplesNacional"),
        "chave_acesso": _t("ChaveAcesso"),  # pode não vir no XML de consulta
    }


# =============================================================================
# Geração do PDF (reportlab canvas — layout fiel à DANFSE do ISS.NET DF)
# =============================================================================
def gerar_danfse_pdf(dados: dict[str, Any]) -> bytes:
    """Renderiza a DANFSE (1 página A4) e retorna os bytes do PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as _canvas

    W, H = A4  # 595.27 x 841.89
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    ML, MR = 22, 22
    x0, x1 = ML, W - MR
    LARG = x1 - x0

    # ---- helpers de desenho ----
    def rect(x, y, w, h):
        c.setLineWidth(0.7)
        c.rect(x, y, w, h)

    def linha(x, y, x2, y2):
        c.setLineWidth(0.5)
        c.line(x, y, x2, y2)

    def txt(x, y, s, size=7, bold=False, color=(0, 0, 0)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.setFillColorRGB(*color)
        c.drawString(x, y, str(s or ""))
        c.setFillColorRGB(0, 0, 0)

    def txt_centro(x, y, w, s, size=7, bold=False):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawCentredString(x + w / 2, y, str(s or ""))

    def barra_titulo(y, texto):
        """Faixa cinza de título de seção; retorna o y do topo do conteúdo."""
        h = 12
        c.setFillColorRGB(0.88, 0.88, 0.88)
        c.rect(x0, y - h, LARG, h, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        rect(x0, y - h, LARG, h)
        txt(x0 + 4, y - h + 3.5, texto, size=7.5, bold=True)
        return y - h

    def campo(x, y, w, label, valor, size=7):
        """Rótulo pequeno + valor abaixo, dentro de uma célula de largura w."""
        txt(x + 3, y - 7, label, size=5.5, color=(0.35, 0.35, 0.35))
        txt(x + 3, y - 15.5, valor, size=size)

    pe = dados.get("prest_end", {}) or {}
    te = dados.get("tom_end", {}) or {}
    y = H - 20

    # ---------- CABEÇALHO ----------
    hcab = 52
    rect(x0, y - hcab, LARG, hcab)
    # divisória caixa direita (série/número)
    xr = x1 - 120
    linha(xr, y - hcab, xr, y)
    logo = _carregar_logo(dados)
    tx = x0 + 40
    if logo is not None:
        c.drawImage(logo, x0 + 4, y - 42, width=64, height=30,
                    preserveAspectRatio=True, anchor="sw", mask="auto")
        tx = x0 + 74
    txt(tx, y - 12, "Governo do Distrito Federal", size=9, bold=True)
    txt(tx, y - 23, "Secretaria de Estado de Economia do Distrito Federal", size=7.5)
    txt(tx, y - 33, "Fone: () - 156 - Opção 3 - www.sefaz.df.gov.br", size=6.5)
    # caixa direita
    txt_centro(xr, y - 9, 120, "Série do Documento", size=5.5)
    txt_centro(xr, y - 18, 120, "Nota Fiscal de Serviço", size=7, bold=True)
    txt_centro(xr, y - 26, 120, "Eletrônica - NFS-e", size=7, bold=True)
    linha(xr, y - 30, x1, y - 30)
    txt_centro(xr, y - 38, 120, "Número da Nota Fiscal", size=5.5)
    txt_centro(xr, y - 49, 120, dados.get("numero_nfse", ""), size=11, bold=True)
    y -= hcab

    # ---------- DADOS DO PRESTADOR ----------
    y = barra_titulo(y, "Dados do Prestador de Serviço")
    hp = 76
    rect(x0, y - hp, LARG, hp)
    xcx = x1 - 175  # caixa direita (datas/autenticidade + QR)
    linha(xcx, y - hp, xcx, y)
    # esquerda: prestador
    txt(x0 + 5, y - 11, dados.get("prest_razao", ""), size=8, bold=True)
    txt(x0 + 5, y - 20, dados.get("prest_fantasia", ""), size=8, bold=True)
    _end_prest = f"{pe.get('logradouro','')} - {pe.get('bairro','')}".strip(" -")
    txt(x0 + 5, y - 31, _end_prest, size=6.5)
    _cep_prest = f"CEP {_fmt_cep(pe.get('cep'))} - Fone: {dados.get('prest_fone','')} - Brasília/ DF"
    txt(x0 + 5, y - 40, _cep_prest, size=6.5)
    txt(x0 + 5, y - 49, dados.get("prest_email", ""), size=6.5)
    txt(x0 + 5, y - 60, f"Inscrição Municipal {dados.get('prest_im','')} - CPF/CNPJ {_fmt_cnpj(dados.get('prest_cnpj'))}", size=6.5)
    # direita: datas + autenticidade + QR
    qr = _fazer_qr(dados)
    if qr is not None:
        c.drawImage(qr, x1 - 52, y - 52, width=48, height=48, preserveAspectRatio=True, mask="auto")
    cxl = xcx + 4
    txt(cxl, y - 8, "Data de Geração da NFS-e", size=5.5, color=(0.35, 0.35, 0.35))
    txt(cxl, y - 17, _fmt_data(dados.get("data_geracao"), com_hora=True), size=7.5, bold=True)
    txt(cxl, y - 27, "Data de Competência", size=5.5, color=(0.35, 0.35, 0.35))
    txt(cxl, y - 36, _fmt_data(dados.get("data_competencia")), size=7.5, bold=True)
    txt(cxl, y - 46, "Cód. de Autenticidade", size=5.5, color=(0.35, 0.35, 0.35))
    txt(cxl, y - 55, dados.get("codigo_verificacao", ""), size=8, bold=True)
    txt(cxl, y - 65, "Responsável pela Retenção", size=5.5, color=(0.35, 0.35, 0.35))
    txt(cxl, y - 73, dados.get("responsavel_retencao", ""), size=7.5, bold=True)
    y -= hp

    # ---------- IDENTIFICAÇÃO DA NFe ----------
    y = barra_titulo(y, "Identificação da Nota Fiscal Eletrônica")
    hi = 40
    rect(x0, y - hi, LARG, hi)
    linha(x0, y - 20, x1, y - 20)  # 2 linhas
    # linha 1: natureza | num rps | serie rps | data emissao rps
    c1 = LARG * 0.30
    for xx in (x0 + c1, x0 + c1 + 90, x0 + c1 + 200):
        linha(xx, y - 20, xx, y)
    campo(x0, y, c1, "Natureza da Operação", dados.get("natureza_operacao", ""))
    campo(x0 + c1, y, 90, "Número do RPS", dados.get("numero_rps", ""))
    campo(x0 + c1 + 90, y, 110, "Série do RPS", dados.get("serie_rps", ""))
    campo(x0 + c1 + 200, y, LARG - c1 - 200, "Data de Emissão do RPS", dados.get("data_emissao_rps", ""))
    # linha 2: local | municipio incidencia
    linha(x0 + LARG * 0.5, y - 40, x0 + LARG * 0.5, y - 20)
    campo(x0, y - 20, LARG * 0.5, "Local dos Serviços", "Brasília - Distrito Federal")
    campo(x0 + LARG * 0.5, y - 20, LARG * 0.5, "Município Incidência", "Brasília - Distrito Federal")
    y -= hi

    # ---------- DADOS DO TOMADOR ----------
    y = barra_titulo(y, "Dados do Tomador de Serviços")
    ht = 72
    rect(x0, y - ht, LARG, ht)
    xmid = x0 + LARG * 0.62
    txt(x0 + 4, y - 9, "CNPJ/CPF :", size=6, bold=True); txt(x0 + 55, y - 9, _fmt_cnpj(dados.get("tom_cnpj")), size=7)
    txt(xmid, y - 9, "IM :", size=6, bold=True); txt(xmid + 30, y - 9, dados.get("tom_im", ""), size=7)
    txt(x0 + 4, y - 20, "Razão Social :", size=6, bold=True); txt(x0 + 62, y - 20, dados.get("tom_razao", ""), size=7)
    txt(x0 + 4, y - 31, "Endereço :", size=6, bold=True); txt(x0 + 55, y - 31, te.get("logradouro", ""), size=7)
    txt(xmid, y - 31, "Número :", size=6, bold=True); txt(xmid + 45, y - 31, te.get("numero", ""), size=7)
    txt(x0 + 4, y - 42, "Complemento :", size=6, bold=True); txt(x0 + 66, y - 42, (te.get("complemento", "") or "")[:70], size=6.5)
    txt(xmid, y - 42, "Bairro :", size=6, bold=True); txt(xmid + 45, y - 42, te.get("bairro", ""), size=7)
    txt(x0 + 4, y - 53, "CEP :", size=6, bold=True); txt(x0 + 55, y - 53, _fmt_cep(te.get("cep")), size=7)
    txt(xmid, y - 53, "Cidade/UF :", size=6, bold=True); txt(xmid + 55, y - 53, f"Brasília/ {te.get('uf','DF')}", size=7)
    txt(x0 + 4, y - 64, "Telefone :", size=6, bold=True); txt(x0 + 55, y - 64, dados.get("tom_fone", ""), size=7)
    txt(xmid, y - 64, "E-mail :", size=6, bold=True); txt(xmid + 45, y - 64, dados.get("tom_email", ""), size=7)
    y -= ht

    # ---------- INTERMEDIÁRIO ----------
    y = barra_titulo(y, "Dados do Intermediário de Serviços")
    hint = 26
    rect(x0, y - hint, LARG, hint)
    txt(x0 + 4, y - 8, "CNPJ/CPF", size=5.5, color=(0.35, 0.35, 0.35))
    txt(x0 + LARG * 0.34, y - 8, "Inscrição Municipal", size=5.5, color=(0.35, 0.35, 0.35))
    txt(x0 + LARG * 0.64, y - 8, "Razão Social", size=5.5, color=(0.35, 0.35, 0.35))
    y -= hint

    # ---------- DESCRIÇÃO DOS SERVIÇOS ----------
    y = barra_titulo(y, "Descrição dos Serviços")
    hd = 92
    rect(x0, y - hd, LARG, hd)
    _texto_multilinha(c, dados.get("descricao", ""), x0 + 5, y - 11, LARG - 10, size=7, leading=9)
    y -= hd

    # ---------- DETALHAMENTO DOS TRIBUTOS ----------
    y = barra_titulo(y, "Detalhamento dos Tributos")
    htr = 84
    rect(x0, y - htr, LARG, htr)
    # linha atividade (mais alta p/ a descrição caber em até 2 linhas sem cortar)
    ha = 26
    linha(x0, y - ha, x1, y - ha)
    desc_ativ = f"{dados.get('cod_trib_mun','')} - {dados.get('desc_cod_trib_mun','') or ''}"[:200]
    # Colunas dimensionadas para fechar EXATAMENTE em x1 (a última ocupa o resto):
    # Atividade | Alíquota | Item LC116 | Cód. NBS | Cód. CNAE
    ca = LARG * 0.56
    w_aliq, w_item, w_nbs = 48, 72, 55
    x_aliq = x0 + ca
    x_item = x_aliq + w_aliq
    x_nbs = x_item + w_item
    x_cnae = x_nbs + w_nbs
    w_cnae = x1 - x_cnae
    for xx in (x_aliq, x_item, x_nbs, x_cnae):
        linha(xx, y - ha, xx, y)
    # Atividade: rótulo + descrição quebrada em até 2 linhas dentro da coluna.
    txt(x0 + 3, y - 7, "Atividade do Município", size=5.5, color=(0.35, 0.35, 0.35))
    _linhas_ativ = _quebrar_em_linhas(desc_ativ, ca - 6, 6.5)
    if len(_linhas_ativ) > 2:  # estoura 2 linhas: corta e sinaliza com reticências
        _linhas_ativ = _linhas_ativ[:2]
        _linhas_ativ[1] = _linhas_ativ[1].rstrip() + "…"
    _yd = y - 15.5
    for _l in _linhas_ativ[:2]:
        txt(x0 + 3, _yd, _l, size=6.5)
        _yd -= 8
    campo(x_aliq, y, w_aliq, "Alíquota", (dados.get("aliquota", "") or "").replace(".", ","))
    campo(x_item, y, w_item, "Item da LC116/2003", dados.get("item_lista", "").replace(".", "") if dados.get("item_lista") else "")
    campo(x_nbs, y, w_nbs, "Cód. NBS", "")
    campo(x_cnae, y, w_cnae, "Cód. CNAE", dados.get("cod_cnae", ""))
    # 2 linhas de valores (7 colunas cada)
    def _linha_valores(yy, cols):
        cw = LARG / len(cols)
        for i, (lab, val) in enumerate(cols):
            xx = x0 + i * cw
            if i:
                linha(xx, yy - 19, xx, yy)
            txt(xx + 2, yy - 7, lab, size=5, color=(0.35, 0.35, 0.35))
            txt(xx + 2, yy - 16, val, size=6.5, bold=True)
    y2 = y - ha
    linha(x0, y2 - 19, x1, y2 - 19)
    _linha_valores(y2, [
        ("VI. Total dos Serviços", _brl(dados.get("valor_servicos"))),
        ("Desconto Incondicionado", _brl(0)),
        ("Deduções Base Cálculo", _brl(0)),
        ("Base de Cálculo", _brl(dados.get("base_calculo"))),
        ("Total do ISSQN", _brl(0)),
        ("ISSQN Retido", dados.get("iss_retido", "")),
        ("Desconto Condicionado", _brl(0)),
    ])
    y3 = y2 - 19
    linha(x0, y3 - 19, x1, y3 - 19)
    _linha_valores(y3, [
        ("PIS", _brl(0)), ("COFINS", _brl(0)), ("INSS", _brl(0)), ("IRRF", _brl(0)),
        ("CSLL", _brl(0)), ("Outras Retenções", _brl(0)),
        ("Vl. ISSQN Retido", _brl(dados.get("valor_iss"))),
        ("Vl. Líquido da Nota Fiscal", _brl(dados.get("valor_liquido"))),
    ])
    # última faixa: Construção Civil | Cód. Obra | Art.
    txt(x0 + 3, y - htr + 4, "Construção Civil :", size=6.5, bold=True)
    txt(x0 + LARG * 0.5, y - htr + 4, "Cód. Obra :", size=6.5, bold=True)
    txt(x0 + LARG * 0.8, y - htr + 4, "Art. :", size=6.5, bold=True)
    y -= htr

    # ---------- INFORMAÇÕES ADICIONAIS ----------
    y = barra_titulo(y, "Informações Adicionais")
    hia = 60
    rect(x0, y - hia, LARG, hia)
    info = dados.get("outras_informacoes", "") or ""
    info = info.replace("\\s\\n", "\n").replace("\\n", "\n")
    yy = _texto_multilinha(c, info, x0 + 5, y - 10, LARG - 10, size=6.5, leading=8)
    chave = dados.get("chave_acesso", "")
    if chave:
        txt(x0 + 5, y - hia + 6, f"Chave de acesso no Ambiente de Dados Nacional: {chave}", size=7, bold=True, color=(0.85, 0.05, 0.05))
    y -= hia

    # ---------- RODAPÉ ----------
    txt_centro(x0, y - 11, LARG, "Consulte a autenticidade deste documento acessando o site: https://iss.fazenda.df.gov.br/online/Login/Login.aspx", size=6.5, bold=True)
    txt_centro(x0, y - 20, LARG, "ISS.NET - Sistema Nota Control®  •  www.notacontrol.com.br", size=6.5)

    c.showPage()
    c.save()
    return buf.getvalue()


def _quebrar_em_linhas(texto: str, largura: float, size: float, fonte: str = "Helvetica") -> list[str]:
    """Quebra `texto` em linhas que cabem em `largura` (por palavra e por \\n)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    linhas: list[str] = []
    for paragrafo in str(texto or "").split("\n"):
        atual = ""
        for palavra in paragrafo.split(" "):
            teste = (atual + " " + palavra).strip()
            if not atual or stringWidth(teste, fonte, size) <= largura:
                atual = teste
            else:
                linhas.append(atual)
                atual = palavra
        if atual:
            linhas.append(atual)
    return linhas


def _texto_multilinha(c, texto: str, x: float, y: float, largura: float, size=7, leading=9) -> float:
    """Escreve texto quebrando por largura (e por \\n). Retorna o y final."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    c.setFont("Helvetica", size)
    for paragrafo in str(texto or "").split("\n"):
        palavras = paragrafo.split(" ")
        linha_atual = ""
        for p in palavras:
            teste = (linha_atual + " " + p).strip()
            if stringWidth(teste, "Helvetica", size) <= largura:
                linha_atual = teste
            else:
                c.drawString(x, y, linha_atual)
                y -= leading
                linha_atual = p
        if linha_atual:
            c.drawString(x, y, linha_atual)
            y -= leading
    return y


def _carregar_logo(dados: dict[str, Any]):
    """Logo do prestador (ImageReader) para o cabeçalho, ou None se indisponível.

    Contrato de dados['logo_path']:
      - ausente/None  → cai no logo da MegaTeam (conveniência do teste standalone);
      - "" (vazio)    → SEM logo (usado pelo e-mail quando o prestador não é MegaTeam);
      - caminho       → usa esse arquivo.
    Retorna None em silêncio se o arquivo/reportlab não estiverem disponíveis — o
    PDF é gerado sem logo, sem quebrar.
    """
    try:
        from reportlab.lib.utils import ImageReader
    except Exception:
        return None
    caminho = dados.get("logo_path")
    if caminho is None:
        padrao = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "logo_megateam_claro.png",
        )
        caminho = padrao if os.path.exists(padrao) else None
    if not caminho or not os.path.exists(caminho):
        return None
    try:
        return ImageReader(caminho)
    except Exception:
        return None


def _fazer_qr(dados: dict[str, Any]):
    """Gera a imagem do QR Code (ImageReader) apontando pro portal de verificação."""
    try:
        import qrcode
        from reportlab.lib.utils import ImageReader
    except Exception:
        return None
    chave = dados.get("chave_acesso") or ""
    if chave:
        conteudo = f"https://www.nfse.gov.br/consultapublica?chave={chave}"
    else:
        conteudo = (
            "https://iss.fazenda.df.gov.br/online/Login/Login.aspx"
            f"?nf={dados.get('numero_nfse','')}&cod={dados.get('codigo_verificacao','')}"
        )
    img = qrcode.make(conteudo)
    b = io.BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
    return ImageReader(b)
