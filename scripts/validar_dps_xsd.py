#!/usr/bin/env python3
"""Valida o XML da DPS contra o XSD v1.01 de forma robusta.

Estratégia:
    1. Lê o XSD v1.01 e REMOVE a referência ao xmldsig (que causa erro de resolução)
    2. Gera um DPS de teste usando a mesma lógica do emitir_nfse_manual.py
    3. Extrai o conteúdo de <nfseDadosMsg> (DPS sem envelope SOAP)
    4. Remove a <Signature> do XML (não podemos validar sem xmldsig XSD)
    5. Valida o XML contra o XSD modificado
    6. Adicionalmente, valida patterns de cada campo individualmente

Uso:
    python scripts/validar_dps_xsd.py
    python scripts/validar_dps_xsd.py --arquivo nfse_emitidas/dps_xxx_enviada.xml
"""
import sys
import re
import argparse
from pathlib import Path

# Adicionar raiz ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxml import etree

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"

XSD_PATH = ROOT / "docs" / "exemplos_oficiais" / "schema_v101_fixed.xsd.xml"


def _carregar_xsd_sem_xmldsig() -> etree.XMLSchema:
    """Carrega o XSD v1.01 removendo a referência ao xmldsig."""
    xsd_text = XSD_PATH.read_text(encoding="utf-8")

    # Remover o import do xmldsig
    xsd_text = re.sub(
        r'<xsd:import\s+namespace="http://www\.w3\.org/2000/09/xmldsig#"[^/]*/>\s*',
        '',
        xsd_text,
    )

    # Remover a referência ao elemento dsig:Signature no TCDPS
    xsd_text = re.sub(
        r'<xsd:element\s+ref="dsig:Signature"[^/]*/>\s*',
        '',
        xsd_text,
    )

    # Remover o namespace dsig do root element se houver
    # (não é estritamente necessário mas evita warning)

    xsd_doc = etree.fromstring(xsd_text.encode("utf-8"))
    return etree.XMLSchema(xsd_doc)


def _extrair_dps_de_soap(xml_text: str) -> etree._Element:
    """Extrai o elemento <DPS> de dentro do envelope SOAP."""
    root = etree.fromstring(xml_text.encode("utf-8"))

    # Tentar encontrar <DPS> em vários caminhos
    dps = root.find(f".//{{{NFSE_NS}}}DPS")
    if dps is None:
        dps = root.find(".//DPS")
    if dps is None:
        raise ValueError("Não encontrei <DPS> no XML fornecido")

    return dps


def _remover_signature(dps: etree._Element) -> None:
    """Remove <Signature> do DPS (não conseguimos validar sem xmldsig XSD)."""
    for sig in dps.findall(f"{{{DSIG_NS}}}Signature"):
        dps.remove(sig)
    for sig in dps.findall("Signature"):
        dps.remove(sig)


def _validar_patterns_individuais(dps: etree._Element) -> list[str]:
    """Valida patterns de campos específicos manualmente."""
    erros = []
    ns = NFSE_NS
    prefix = f"{{{ns}}}"

    # Definições: (xpath relativo a DPS, nome, pattern regex, descrição)
    CHECKS = [
        (f".//{prefix}infDPS", "@Id", r"^DPS[0-9]{42}$", "TSIdDPS: DPS + 42 dígitos = 45 chars"),
        (f".//{prefix}nDPS", None, r"^[1-9]{1}[0-9]{0,14}$", "TSNumDPS: sem zeros à esquerda, 1-15 dígitos"),
        (f".//{prefix}serie", None, r"^0{0,4}\d{1,5}$", "TSSerieDPS: até 5 chars"),
        (f".//{prefix}cTribNac", None, r"^[0-9]{6}$", "TSCodTribNac: exatamente 6 dígitos"),
        (f".//{prefix}vServ", None, r"^(0|0\.[0-9]{2}|[1-9]{1}[0-9]{0,14}(\.[0-9]{2})?)$", "TSDec15V2"),
        (f".//{prefix}pAliq", None, r"^(0|[0-9]{1}(\.[0-9]{2})?)$", "TSDec1V2: 1 dígito + 2 decimais"),
        (f".//{prefix}pTotTribSN", None, r"^(0|0\.[0-9]{2}|[1-9]{1}[0-9]{0,1}(\.[0-9]{2})?)$", "TSDec2V2: 1-2 dígitos + 2 decimais"),
    ]

    for xpath, attr, pattern, desc in CHECKS:
        elements = dps.findall(xpath)
        for el in elements:
            if attr:
                value = el.get(attr.lstrip("@"), "")
            else:
                value = (el.text or "").strip()
            if not value:
                continue
            if not re.match(pattern, value):
                tag = etree.QName(el).localname
                field = f"{tag}.{attr}" if attr else tag
                erros.append(f"  ❌ {field} = '{value}' NÃO bate com {desc} (pattern: {pattern})")
            else:
                tag = etree.QName(el).localname
                field = f"{tag}.{attr}" if attr else tag
                print(f"  ✅ {field} = '{value}' — OK ({desc})")

    return erros


def _verificar_choice_totTrib(dps: etree._Element) -> list[str]:
    """Verifica que <totTrib> tem exatamente 1 filho (é um choice)."""
    erros = []
    for tot in list(dps.iter(f"{{{NFSE_NS}}}totTrib")) + list(dps.iter("totTrib")):
        filhos = [etree.QName(f).localname for f in tot]
        if len(filhos) > 1:
            erros.append(f"  ❌ <totTrib> tem {len(filhos)} filhos ({filhos}) mas é um <choice> — deve ter apenas 1")
        elif len(filhos) == 1:
            print(f"  ✅ <totTrib> tem 1 filho ({filhos[0]}) — OK (choice exclusivo)")
        else:
            erros.append(f"  ❌ <totTrib> está vazio — deve ter 1 filho")
    return erros


def _verificar_locPrest_sem_opConsumServ(dps: etree._Element) -> list[str]:
    """Verifica que <locPrest> NÃO contém <opConsumServ> (inexistente no v1.01)."""
    erros = []
    for loc in list(dps.iter(f"{{{NFSE_NS}}}locPrest")) + list(dps.iter("locPrest")):
        filhos = [etree.QName(f).localname for f in loc]
        if "opConsumServ" in filhos:
            erros.append(f"  ❌ <locPrest> contém <opConsumServ> — este campo NÃO existe no XSD v1.01!")
        else:
            print(f"  ✅ <locPrest> sem opConsumServ — OK (filhos: {filhos})")
    return erros


def _verificar_ordem_infDPS(dps: etree._Element) -> list[str]:
    """Verifica a ordem dos elementos dentro de <infDPS>."""
    erros = []
    ORDEM_INFDPS = [
        "tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet",
        "tpEmit", "cMotivoEmisTI", "chNFSeRej", "cLocEmi", "subst",
        "prest", "toma", "interm", "serv", "valores", "IBSCBS", "pag",
    ]

    inf = dps.find(f"{{{NFSE_NS}}}infDPS")
    if inf is None:
        inf = dps.find("infDPS")
    if inf is None:
        erros.append("  ❌ <infDPS> não encontrado!")
        return erros

    filhos = [etree.QName(f).localname for f in inf]
    # Filtrar para apenas os que estão na ordem esperada
    filhos_conhecidos = [f for f in filhos if f in ORDEM_INFDPS]

    ultimo_idx = -1
    for f in filhos_conhecidos:
        idx = ORDEM_INFDPS.index(f)
        if idx < ultimo_idx:
            erros.append(f"  ❌ <{f}> está fora de ordem em <infDPS>! Ordem esperada: {ORDEM_INFDPS}")
            break
        ultimo_idx = idx

    if not erros:
        print(f"  ✅ Ordem de <infDPS> correta: {filhos_conhecidos}")

    return erros


def _verificar_ordem_tribMun(dps: etree._Element) -> list[str]:
    """Verifica a ordem dos filhos de <tribMun>."""
    erros = []
    ORDEM = ["tribISSQN", "cPaisResult", "tpImunidade", "exigSusp", "BM", "tpRetISSQN", "pAliq"]

    for tm in list(dps.iter(f"{{{NFSE_NS}}}tribMun")) + list(dps.iter("tribMun")):
        filhos = [etree.QName(f).localname for f in tm]
        filhos_conhecidos = [f for f in filhos if f in ORDEM]
        ultimo_idx = -1
        for f in filhos_conhecidos:
            idx = ORDEM.index(f)
            if idx < ultimo_idx:
                erros.append(f"  ❌ <tribMun> fora de ordem: {filhos} (esperado: {ORDEM})")
                break
            ultimo_idx = idx
        if not any("tribMun" in e for e in erros):
            print(f"  ✅ Ordem de <tribMun> correta: {filhos}")

    return erros


def gerar_dps_teste() -> str:
    """Gera um DPS de teste usando a mesma lógica do emitir_nfse_manual.py."""
    from src.nfse_df import emitir_nfse
    from src.config import settings

    # Dados fictícios (mesmo do --exemplo)
    invoice = {
        "id": "teste_validacao_xsd",
        "total_cents": 185000,
        "due_date": "2026-04-20",
        "items_description": "Suporte técnico em informática",
        "payer_name": "Empresa Tomadora LTDA",
        "payer_cpf_cnpj": "50214976000135",
        "payer_email": "financeiro@tomadora.com.br",
    }
    endereco = {
        "logradouro": "SCS Quadra 1",
        "numero": "100",
        "complemento": "Sala 500",
        "bairro": "Asa Sul",
        "cidade": "Brasília",
        "uf": "DF",
        "cep": "70300000",
    }

    # Não enviar, apenas gerar
    # Para isso precisamos chamar as funções internas
    from src.nfse_df import (
        _montar_xml_dps,
        _proximo_numero_dps,
        Empresa,
        DadosServico,
        _patch_xml_para_v101,
    )

    empresa = Empresa(
        cnpj="36342291000143",
        razao_social="MEGASUPORTE SERVIÇOS DE TI LTDA",
        codigo_servico="01.07",
        descricao_servico="Suporte técnico em informática",
        aliquota_iss=2.0,
    )
    servico = DadosServico(
        codigo_servico="01.07",
        descricao="Suporte técnico em informática",
        valor_cents=185000,
        aliquota_iss=2.0,
    )
    numero = _proximo_numero_dps()
    xml_dps, dps_id = _montar_xml_dps(
        empresa=empresa,
        servico=servico,
        endereco_tomador=endereco,
        invoice=invoice,
        numero_dps=numero,
    )
    return xml_dps


def main():
    parser = argparse.ArgumentParser(description="Valida DPS contra XSD v1.01")
    parser.add_argument("--arquivo", help="XML enviado (envelope SOAP) para validar")
    parser.add_argument("--gerar", action="store_true", help="Gerar DPS de teste e validar")
    args = parser.parse_args()

    print("=" * 70)
    print("VALIDAÇÃO DO XML DA DPS CONTRA XSD v1.01")
    print("=" * 70)

    # 1. Carregar XSD
    print("\n📋 Carregando XSD v1.01 (sem xmldsig)...")
    try:
        schema = _carregar_xsd_sem_xmldsig()
        print("  ✅ XSD carregado com sucesso")
    except Exception as e:
        print(f"  ❌ Erro ao carregar XSD: {e}")
        sys.exit(1)

    # 2. Obter XML da DPS
    if args.arquivo:
        print(f"\n📄 Lendo XML de: {args.arquivo}")
        xml_text = Path(args.arquivo).read_text(encoding="utf-8")
        dps = _extrair_dps_de_soap(xml_text)
    elif args.gerar:
        print("\n🔨 Gerando DPS de teste...")
        xml_text = gerar_dps_teste()
        dps = etree.fromstring(xml_text.encode("utf-8"))
    else:
        # Pegar o mais recente
        emitidas = sorted(Path(ROOT / "nfse_emitidas").glob("*_enviada_*.xml"))
        if emitidas:
            arquivo = emitidas[-1]
            print(f"\n📄 Usando XML mais recente: {arquivo.name}")
            xml_text = arquivo.read_text(encoding="utf-8")
            dps = _extrair_dps_de_soap(xml_text)
        else:
            print("\n🔨 Nenhum XML enviado encontrado, gerando DPS de teste...")
            xml_text = gerar_dps_teste()
            dps = etree.fromstring(xml_text.encode("utf-8"))

    # 3. Remover Signature
    _remover_signature(dps)

    # 4. Mostrar XML limpo
    print("\n📝 XML da DPS (sem Signature):")
    xml_limpo = etree.tostring(dps, encoding="unicode", pretty_print=True)
    print(xml_limpo)

    # 5. Validar contra XSD
    print("\n" + "=" * 70)
    print("VALIDAÇÃO XSD")
    print("=" * 70)

    # Wrap em GerarNfseEnvio (o XSD espera esse root)
    wrapper = etree.Element(f"{{{NFSE_NS}}}GerarNfseEnvio")
    wrapper.append(dps)

    is_valid = schema.validate(wrapper)
    if is_valid:
        print("  ✅ XML VÁLIDO contra o XSD v1.01!")
    else:
        print(f"  ❌ XML INVÁLIDO — {len(schema.error_log)} erro(s):")
        for err in schema.error_log:
            print(f"    Linha {err.line}: {err.message}")

    # 6. Validações manuais de patterns
    print("\n" + "=" * 70)
    print("VALIDAÇÃO DE PATTERNS (tipos de dados)")
    print("=" * 70)
    erros_patterns = _validar_patterns_individuais(dps)

    # 7. Validações estruturais
    print("\n" + "=" * 70)
    print("VALIDAÇÃO ESTRUTURAL")
    print("=" * 70)
    erros_estrut = []
    erros_estrut.extend(_verificar_choice_totTrib(dps))
    erros_estrut.extend(_verificar_locPrest_sem_opConsumServ(dps))
    erros_estrut.extend(_verificar_ordem_infDPS(dps))
    erros_estrut.extend(_verificar_ordem_tribMun(dps))

    # Resumo
    total_erros = len(erros_patterns) + len(erros_estrut) + (0 if is_valid else len(schema.error_log))
    print("\n" + "=" * 70)
    if total_erros == 0:
        print("✅ TUDO OK — nenhum erro encontrado!")
    else:
        print(f"❌ {total_erros} ERRO(S) ENCONTRADO(S)")
        for e in erros_patterns + erros_estrut:
            print(e)
    print("=" * 70)

    sys.exit(0 if total_erros == 0 else 1)


if __name__ == "__main__":
    main()
