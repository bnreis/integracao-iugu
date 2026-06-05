#!/usr/bin/env python3
"""Valida o XML do RPS (ABRASF 2.04) contra o XSD oficial 'schema nfse v2-04.xsd'.

Espelha scripts/validar_dps_xsd.py, mas para o backend ABRASF 2.04 (Parte B do
ADR-0005). Roda 100% OFFLINE — não toca em nenhuma API, não assina, não envia.

Estratégia:
    1. Carrega o XSD ABRASF 2.04 e REMOVE a referência ao xmldsig (o arquivo
       xmldsig-core-schema20020212.xsd não está no projeto e a Signature é
       opcional no schema — validamos o RPS NÃO assinado).
    2. Gera um GerarNfseEnvio de exemplo via _montar_xml_rps_abrasf (mesma lógica
       do emitir_abrasf204), SEM assinar.
    3. Valida o GerarNfseEnvio contra o XSD modificado.
    4. Valida patterns/ordem de alguns campos sensíveis (Aliquota, ItemListaServico, etc.).

Uso:
    python scripts/validar_rps_xsd.py
    python scripts/validar_rps_xsd.py --arquivo nfse_emitidas/rps_xxx_enviado.xml

Dica de encoding (console Windows): se reclamar de UnicodeEncodeError, rode com
    PYTHONUTF8=1 python scripts/validar_rps_xsd.py
"""
import sys
import re
import argparse
from pathlib import Path

# Força UTF-8 na saída (console Windows costuma ser cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Adiciona a raiz do projeto ao path (scripts/ -> raiz)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxml import etree

# Namespace do schema ABRASF 2.04 (targetNamespace do XSD).
ABRASF_NS = "http://www.abrasf.org.br/nfse.xsd"
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"

XSD_PATH = ROOT / "docs" / "exemplos_oficiais" / "abrasf204" / "schema" / "schema nfse v2-04.xsd"


def _carregar_xsd_sem_xmldsig(aceitar_signature: bool = False) -> etree.XMLSchema:
    """Carrega o XSD ABRASF 2.04 lidando com a dependência do xmldsig.

    O XSD importa 'xmldsig-core-schema20020212.xsd' (ausente no projeto) e
    referencia 'dsig:Signature' em vários tipos.

    Dois modos:
      aceitar_signature=False (default — usado por gerar_rps_teste, sem assinatura):
        Remove tanto o <xsd:import> quanto TODAS as refs a dsig:Signature.
        Útil para validar o RPS NÃO assinado.

      aceitar_signature=True (usado por --com-assinatura):
        Remove o <xsd:import> mas SUBSTITUI cada ref="dsig:Signature" por um
        <xsd:any namespace="http://www.w3.org/2000/09/xmldsig#" processContents="skip">,
        preservando minOccurs/maxOccurs. Isso permite que a Signature seja aceita
        EXATAMENTE no lugar onde o XSD ABRASF a espera (filha de Rps), sem
        precisar resolver o schema oficial xmldsig. Se a Signature estiver em
        outro lugar (ex.: filha de GerarNfseEnvio), a validação falha — que é
        justamente o sintoma que queremos detectar.

    Bug do XSD oficial: o elemento global <xsd:element name="CompNfse" ... /> é
    declarado com minOccurs/maxOccurs, atributos que NÃO são permitidos em
    elementos top-level segundo XSD 1.0. lxml/libxml2 recusa carregar o schema.
    Removemos esses atributos do elemento global (eles continuam valendo nas
    referências <xsd:element ref="CompNfse" .../> dentro de complex types).
    """
    xsd_text = XSD_PATH.read_text(encoding="utf-8")

    # Remove o import do xmldsig (pode ou não ter espaço antes do '/>').
    xsd_text = re.sub(
        r'<xsd:import\s+namespace="http://www\.w3\.org/2000/09/xmldsig#"[^>]*?/>\s*',
        '',
        xsd_text,
    )

    if aceitar_signature:
        # Substitui cada <xsd:element ref="dsig:Signature" ... /> por um
        # <xsd:any namespace="...xmldsig#" processContents="skip" ... />, mantendo
        # os atributos remanescentes (minOccurs/maxOccurs/etc.).
        def _swap(m: re.Match) -> str:
            attrs = m.group("attrs") or ""
            return (
                f'<xsd:any namespace="http://www.w3.org/2000/09/xmldsig#" '
                f'processContents="skip"{attrs}/>'
            )

        xsd_text = re.sub(
            r'<xsd:element\s+ref="dsig:Signature"(?P<attrs>[^/>]*)/>',
            _swap,
            xsd_text,
            flags=re.DOTALL,
        )
    else:
        # Remove todas as referências ao elemento dsig:Signature (multi-linha).
        xsd_text = re.sub(
            r'<xsd:element\s+ref="dsig:Signature"[^>]*?/>\s*',
            '',
            xsd_text,
            flags=re.DOTALL,
        )

    # Sanitiza o elemento GLOBAL CompNfse: remove minOccurs/maxOccurs (inválidos
    # em element top-level). Não afeta as refs internas, que mantêm seus quantificadores.
    xsd_text = re.sub(
        r'(<xsd:element\s+name="CompNfse"\s+type="tcCompNfse")\s+minOccurs="[^"]*"\s+maxOccurs="[^"]*"',
        r'\1',
        xsd_text,
        flags=re.DOTALL,
    )

    xsd_doc = etree.fromstring(xsd_text.encode("utf-8"))
    return etree.XMLSchema(xsd_doc)


def _remover_signature(el: etree._Element) -> None:
    """Remove qualquer <Signature> (caso o XML de entrada já esteja assinado)."""
    for sig in el.findall(f".//{{{DSIG_NS}}}Signature"):
        sig.getparent().remove(sig)
    for sig in el.findall(".//Signature"):
        sig.getparent().remove(sig)


def _extrair_envio_de_soap(xml_text: str) -> etree._Element:
    """Extrai o <GerarNfseEnvio> de dentro de um envelope SOAP (ou retorna a raiz)."""
    root = etree.fromstring(xml_text.encode("utf-8"))
    if etree.QName(root).localname == "GerarNfseEnvio":
        return root
    envio = root.find(f".//{{{ABRASF_NS}}}GerarNfseEnvio")
    if envio is None:
        envio = root.find(".//GerarNfseEnvio")
    if envio is None:
        raise ValueError("Não encontrei <GerarNfseEnvio> no XML fornecido")
    return envio


def _validar_patterns(envio: etree._Element) -> list[str]:
    """Valida patterns/valores de campos sensíveis do RPS ABRASF 2.04."""
    erros: list[str] = []
    p = f"{{{ABRASF_NS}}}"

    CHECKS = [
        (f".//{p}Aliquota", r"^\d{1,2}(\.\d{1,2})?$", "tsAliquota: decimal totalDigits=4, frac=2 (ex.: 2.00)"),
        (f".//{p}ItemListaServico", r"^\d{2}\.\d{2}$", "tsItemListaServico: 'NN.NN' (LC 116/2003)"),
        (f".//{p}ValorServicos", r"^\d{1,13}\.\d{2}$", "tsValor: decimal com 2 casas"),
        (f".//{p}CodigoMunicipio", r"^\d{1,7}$", "tsCodigoMunicipioIbge: int até 7 dígitos"),
        (f".//{p}Cep", r"^\d{8}$", "tsCep: 8 dígitos"),
        (f".//{p}Serie", r"^.{1,5}$", "tsSerieRps: 1-5 chars"),
    ]
    for xpath, pattern, desc in CHECKS:
        for el in envio.findall(xpath):
            value = (el.text or "").strip()
            if not value:
                continue
            tag = etree.QName(el).localname
            if re.match(pattern, value):
                print(f"  [OK] {tag} = '{value}' ({desc})")
            else:
                erros.append(f"  [X] {tag} = '{value}' NAO bate com {desc} (pattern: {pattern})")
    return erros


def _gerar_dados_teste():
    """Constrói os 3 objetos (invoice/endereco/empresa/servico/numero) usados
    pelos modos de teste (sem assinatura e com assinatura). Isola para evitar
    duplicação entre as duas funções de geração."""
    from src.nfse_df import _proximo_numero_rps, Empresa, DadosServico

    invoice = {
        "id": "teste_validacao_rps",
        "total_cents": 185000,
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
    empresa = Empresa(
        cnpj="50214976000135",
        razao_social="EMPRESA TOMADORA LTDA",
        email="financeiro@tomadora.com.br",
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
    numero = _proximo_numero_rps()
    return invoice, endereco, empresa, servico, numero


def gerar_rps_teste() -> str:
    """Gera um GerarNfseEnvio de exemplo (NÃO assinado) com a lógica do backend."""
    from src.nfse_df import _montar_xml_rps_abrasf

    invoice, endereco, empresa, servico, numero = _gerar_dados_teste()
    xml_envio, _rps_id = _montar_xml_rps_abrasf(
        empresa=empresa,
        servico=servico,
        endereco_tomador=endereco,
        invoice=invoice,
        numero_rps=numero,
    )
    return xml_envio


def gerar_rps_assinado_teste() -> str:
    """Gera um GerarNfseEnvio ASSINADO e com a Signature reposicionada para
    dentro de <Rps> (Fix 1 do ADR-0005 Parte B).

    Usa o certificado A1 configurado no .env (settings.nfse_certificado_path).
    O caminho de validação confirma que:
      1. _assinar_xml gera Signature válida (XMLDSig enveloped sobre #Id);
      2. _reposicionar_signature_dentro_de_rps move a Signature de
         GerarNfseEnvio (irmã de Rps) para DENTRO de Rps (irmã de
         InfDeclaracaoPrestacaoServico), no lugar exigido pelo XSD ABRASF 2.04.
    """
    from src.nfse_df import (
        _montar_xml_rps_abrasf,
        _assinar_xml,
        _reposicionar_signature_dentro_de_rps,
    )

    invoice, endereco, empresa, servico, numero = _gerar_dados_teste()
    xml_envio, rps_id = _montar_xml_rps_abrasf(
        empresa=empresa,
        servico=servico,
        endereco_tomador=endereco,
        invoice=invoice,
        numero_rps=numero,
    )
    xml_assinado = _assinar_xml(xml_envio, rps_id)
    return _reposicionar_signature_dentro_de_rps(xml_assinado)


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida RPS ABRASF 2.04 contra o XSD")
    parser.add_argument("--arquivo", help="XML (envelope SOAP ou GerarNfseEnvio) para validar")
    parser.add_argument(
        "--com-assinatura",
        action="store_true",
        help=(
            "Gera RPS + assina + reposiciona Signature dentro de <Rps> e valida o "
            "XML ASSINADO contra o XSD (refs a dsig:Signature viram xsd:any). "
            "Confirma o Fix 1 do ADR-0005 Parte B."
        ),
    )
    args = parser.parse_args()

    print("=" * 70)
    print("VALIDACAO DO XML DO RPS CONTRA O XSD ABRASF 2.04 (schema nfse v2-04.xsd)")
    print("=" * 70)

    modo = "com Signature (xsd:any)" if args.com_assinatura else "sem xmldsig"
    print(f"\n[1] Carregando XSD ABRASF 2.04 ({modo})...")
    try:
        schema = _carregar_xsd_sem_xmldsig(aceitar_signature=args.com_assinatura)
        print("  [OK] XSD carregado")
    except Exception as e:
        print(f"  [X] Erro ao carregar XSD: {e}")
        sys.exit(1)

    if args.arquivo:
        print(f"\n[2] Lendo XML de: {args.arquivo}")
        xml_text = Path(args.arquivo).read_text(encoding="utf-8")
        envio = _extrair_envio_de_soap(xml_text)
    elif args.com_assinatura:
        print("\n[2] Gerando GerarNfseEnvio de teste ASSINADO + Signature reposicionada...")
        xml_text = gerar_rps_assinado_teste()
        envio = etree.fromstring(xml_text.encode("utf-8"))
    else:
        print("\n[2] Gerando GerarNfseEnvio de teste (nao assinado)...")
        xml_text = gerar_rps_teste()
        envio = etree.fromstring(xml_text.encode("utf-8"))

    # No modo "com assinatura" PRESERVAMOS a Signature (queremos validar com ela
    # presente no lugar certo). Nos demais modos removemos para validar o
    # esqueleto do RPS sem depender do schema xmldsig.
    if not args.com_assinatura:
        _remover_signature(envio)

    cabecalho_xml = "XML do GerarNfseEnvio" + (
        " (Signature DENTRO de Rps)" if args.com_assinatura else " (sem Signature)"
    )
    print(f"\n[3] {cabecalho_xml}:")
    print(etree.tostring(envio, encoding="unicode", pretty_print=True))

    print("=" * 70)
    print("VALIDACAO XSD")
    print("=" * 70)
    is_valid = schema.validate(envio)
    if is_valid:
        print("  [OK] XML VALIDO contra o XSD ABRASF 2.04!")
    else:
        print(f"  [X] XML INVALIDO -- {len(schema.error_log)} erro(s):")
        for err in schema.error_log:
            print(f"    Linha {err.line}: {err.message}")

    print("\n" + "=" * 70)
    print("VALIDACAO DE PATTERNS")
    print("=" * 70)
    erros_patterns = _validar_patterns(envio)

    total = len(erros_patterns) + (0 if is_valid else len(schema.error_log))
    print("\n" + "=" * 70)
    if total == 0:
        print("[OK] TUDO OK -- nenhum erro encontrado!")
    else:
        print(f"[X] {total} ERRO(S) ENCONTRADO(S)")
        for e in erros_patterns:
            print(e)
    print("=" * 70)
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
