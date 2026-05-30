"""
Script de verificação local: gera um XML DPS v1.01 de exemplo e valida
a estrutura contra o XSD oficial v1.01.

Uso:
    python scripts/verificar_xml_v101.py

NÃO envia ao webservice — apenas verifica a estrutura XML localmente.
"""
import sys
from pathlib import Path

# Garante que o diretório raiz do projeto esteja no path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lxml import etree


def gerar_xml_teste():
    """Gera um XML DPS v1.01 usando o fluxo real do nfse_df.py."""
    from src.nfse_df import _montar_xml_dps, DadosServico
    from src.spreadsheet import Empresa

    # Empresa de teste (tomador)
    empresa_teste = Empresa(
        cnpj="11222333000181",
        razao_social="EMPRESA TESTE LTDA",
        email="teste@teste.com",
        codigo_servico="010700",
        descricao_servico="Suporte técnico em informática",
        aliquota_iss=2.0,
    )

    servico = DadosServico(
        codigo_servico="010700",
        descricao="Suporte técnico em informática - Teste homologação v1.01",
        valor_cents=10000,  # R$ 100,00
        aliquota_iss=2.0,
    )

    endereco_tomador = {
        "logradouro": "Rua Teste",
        "numero": "123",
        "complemento": "Sala 1",
        "bairro": "Centro",
        "cidade": "Brasília",
        "uf": "DF",
        "cep": "70000000",
    }

    invoice_fake = {
        "id": "TESTE-V101",
        "total_paid_cents": 10000,
        "payer_name": "EMPRESA TESTE LTDA",
        "payer_address_street": "Rua Teste",
        "payer_address_number": "123",
        "payer_address_complement": "Sala 1",
        "payer_address_district": "Centro",
        "payer_address_city": "Brasília",
        "payer_address_state": "DF",
        "payer_address_zip_code": "70000-000",
        "items": [{"description": "Suporte técnico mensal"}],
    }

    xml_str, dps_id = _montar_xml_dps(
        empresa=empresa_teste,
        servico=servico,
        endereco_tomador=endereco_tomador,
        invoice=invoice_fake,
        numero_dps="000000000000099",
    )

    return xml_str, dps_id


def validar_contra_xsd(xml_str: str, xsd_path: Path) -> list[str]:
    """Valida o XML contra o XSD. Retorna lista de erros (vazia = sucesso)."""
    try:
        xsd_doc = etree.parse(str(xsd_path))
        xsd_schema = etree.XMLSchema(xsd_doc)
    except Exception as e:
        return [f"Erro ao carregar XSD: {e}"]

    try:
        xml_doc = etree.fromstring(xml_str.encode("utf-8"))
    except Exception as e:
        return [f"Erro ao parsear XML: {e}"]

    if xsd_schema.validate(xml_doc):
        return []

    return [str(err) for err in xsd_schema.error_log]


def verificar_estrutura(xml_str: str) -> list[str]:
    """Verifica manualmente se os elementos-chave v1.01 estão presentes."""
    from lxml import etree

    erros = []
    root = etree.fromstring(xml_str.encode("utf-8"))

    # Detectar namespace
    ns_map = root.nsmap
    ns = ns_map.get(None, "http://www.sped.fazenda.gov.br/nfse")
    nsprefix = f"{{{ns}}}"

    # 1. Verificar versao="1.01"
    dps_el = root if "DPS" in root.tag else root.find(f".//{nsprefix}DPS")
    if dps_el is None:
        erros.append("ERRO: Tag <DPS> não encontrada")
    else:
        versao = dps_el.get("versao")
        if versao != "1.01":
            erros.append(f"ERRO: versao='{versao}', esperado '1.01'")
        else:
            print(f"  OK: versao='1.01'")

    # 2. Verificar Id (45 chars, padrão DPS + 42 dígitos)
    inf_dps = root.find(f".//{nsprefix}infDPS")
    if inf_dps is None:
        inf_dps = root.find(f".//infDPS")
    if inf_dps is not None:
        dps_id = inf_dps.get("Id", "")
        if len(dps_id) != 45:
            erros.append(f"AVISO: Id tem {len(dps_id)} chars, esperado 45: '{dps_id}'")
        elif not dps_id.startswith("DPS"):
            erros.append(f"ERRO: Id não começa com 'DPS': '{dps_id}'")
        else:
            print(f"  OK: Id='{dps_id}' ({len(dps_id)} chars)")

    # 3. Verificar IBSCBS existe
    ibscbs = root.find(f".//{nsprefix}IBSCBS")
    if ibscbs is None:
        ibscbs = root.find(f".//IBSCBS")
    if ibscbs is None:
        erros.append("ERRO: Grupo <IBSCBS> não encontrado no XML")
    else:
        print(f"  OK: <IBSCBS> presente")

        # 3a. finNFSe
        fin = ibscbs.find(f"{nsprefix}finNFSe")
        if fin is None:
            fin = ibscbs.find("finNFSe")
        if fin is None or not fin.text:
            erros.append("ERRO: <finNFSe> ausente ou vazio dentro de <IBSCBS>")
        else:
            print(f"  OK: finNFSe='{fin.text}'")

        # 3b. cIndOp
        c_ind = ibscbs.find(f"{nsprefix}cIndOp")
        if c_ind is None:
            c_ind = ibscbs.find("cIndOp")
        if c_ind is None or not c_ind.text:
            erros.append("ERRO: <cIndOp> ausente ou vazio")
        else:
            print(f"  OK: cIndOp='{c_ind.text}'")

        # 3c. indDest
        ind_dest = ibscbs.find(f"{nsprefix}indDest")
        if ind_dest is None:
            ind_dest = ibscbs.find("indDest")
        if ind_dest is None or not ind_dest.text:
            erros.append("ERRO: <indDest> ausente ou vazio")
        else:
            print(f"  OK: indDest='{ind_dest.text}'")

        # 3d. valores > trib > gIBSCBS > CST + cClassTrib
        valores = ibscbs.find(f"{nsprefix}valores")
        if valores is None:
            valores = ibscbs.find("valores")
        if valores is None:
            erros.append("ERRO: <valores> ausente dentro de <IBSCBS>")
        else:
            trib = valores.find(f"{nsprefix}trib")
            if trib is None:
                trib = valores.find("trib")
            if trib is None:
                erros.append("ERRO: <trib> ausente dentro de <valores> do IBSCBS")
            else:
                g_ibscbs = trib.find(f"{nsprefix}gIBSCBS")
                if g_ibscbs is None:
                    g_ibscbs = trib.find("gIBSCBS")
                if g_ibscbs is None:
                    erros.append("ERRO: <gIBSCBS> ausente dentro de <trib>")
                else:
                    cst = g_ibscbs.find(f"{nsprefix}CST")
                    if cst is None:
                        cst = g_ibscbs.find("CST")
                    cct = g_ibscbs.find(f"{nsprefix}cClassTrib")
                    if cct is None:
                        cct = g_ibscbs.find("cClassTrib")
                    if cst is None or not cst.text:
                        erros.append("ERRO: <CST> ausente ou vazio")
                    else:
                        print(f"  OK: CST='{cst.text}'")
                    if cct is None or not cct.text:
                        erros.append("ERRO: <cClassTrib> ausente ou vazio")
                    else:
                        print(f"  OK: cClassTrib='{cct.text}'")

    # 4. Verificar ordem: IBSCBS vem depois de valores
    if inf_dps is not None:
        children = [etree.QName(c).localname for c in inf_dps]
        if "valores" in children and "IBSCBS" in children:
            idx_val = children.index("valores")
            idx_ibs = children.index("IBSCBS")
            if idx_ibs == idx_val + 1:
                print(f"  OK: <IBSCBS> está logo após <valores> (posição {idx_ibs})")
            else:
                erros.append(
                    f"AVISO: <IBSCBS> na posição {idx_ibs}, <valores> na {idx_val}. "
                    f"IBSCBS deveria estar logo após valores."
                )
        print(f"  INFO: Ordem dos filhos de infDPS: {children}")

    return erros


def main():
    print("=" * 70)
    print("VERIFICAÇÃO XML DPS v1.01 — Integração Iugu/NFS-e DF")
    print("=" * 70)

    # 1. Gerar XML
    print("\n1. Gerando XML DPS v1.01 de teste...")
    try:
        xml_str, dps_id = gerar_xml_teste()
        print(f"   XML gerado com sucesso. Id={dps_id}")
    except Exception as e:
        print(f"   FALHA ao gerar XML: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 2. Salvar para inspeção
    output_path = PROJECT_ROOT / "nfse_emitidas" / "teste_v101_verificacao.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_str, encoding="utf-8")
    print(f"   Salvo em: {output_path}")

    # 3. Verificação estrutural
    print("\n2. Verificação estrutural do XML:")
    erros_estrutura = verificar_estrutura(xml_str)

    # 4. Validação contra XSD (se disponível)
    xsd_path = PROJECT_ROOT / "docs" / "exemplos_oficiais" / "schema_v101.xsd.xml"
    print(f"\n3. Validação contra XSD ({xsd_path.name}):")
    if xsd_path.exists():
        erros_xsd = validar_contra_xsd(xml_str, xsd_path)
        if not erros_xsd:
            print("   XSD: VÁLIDO!")
        else:
            print(f"   XSD: {len(erros_xsd)} erro(s):")
            for err in erros_xsd[:20]:
                print(f"     - {err}")
    else:
        print(f"   XSD não encontrado em {xsd_path}")
        erros_xsd = []

    # 5. Resumo
    total_erros = len(erros_estrutura) + len(erros_xsd)
    print(f"\n{'=' * 70}")
    if total_erros == 0:
        print("RESULTADO: XML v1.01 gerado com sucesso — estrutura OK!")
        print("Próximo passo: testar no validador online do Nota Control ou")
        print("enviar via scripts/emitir_nfse_manual.py --exemplo")
    else:
        print(f"RESULTADO: {total_erros} problema(s) encontrado(s)")
        if erros_estrutura:
            print("\nErros estruturais:")
            for e in erros_estrutura:
                print(f"  - {e}")
    print("=" * 70)

    # Mostrar trecho do XML para inspeção
    print("\nTrecho do XML (primeiros 3000 chars):")
    print("-" * 70)
    # Formatar com indentação
    from lxml import etree
    root = etree.fromstring(xml_str.encode("utf-8"))
    etree.indent(root, space="  ")
    formatted = etree.tostring(root, encoding="unicode", xml_declaration=False)
    print(formatted[:3000])
    if len(formatted) > 3000:
        print(f"\n... ({len(formatted) - 3000} chars restantes, ver arquivo completo)")


if __name__ == "__main__":
    main()
