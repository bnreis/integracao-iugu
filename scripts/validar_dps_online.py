"""
Valida um XML de DPS contra o validador oficial online do Nota Control,
SEM precisar enviar ao webservice de emissão.

O validador online (disponibilizado pelo Nota Control) aceita o XML da DPS
via HTTP POST e retorna a lista de erros de schema encontrados. É a forma
mais rápida de iterar na construção da DPS sem esgotar tentativas reais.

Endpoint oficial (do Manual de Integração v1.01, seção 14):
    https://nfse.issnetonline.com.br/wsnfsenacional/homologacao/validarxml

Uso:
    # Validar um XML de DPS específico
    python scripts/validar_dps_online.py nfse_emitidas/dps_000000000000012_dps_assinada_*.xml

    # Validar o mais recente automaticamente
    python scripts/validar_dps_online.py --latest

    # Validar o envelope SOAP completo (em vez de só a DPS)
    python scripts/validar_dps_online.py --latest --envelope

Observações:
- O validador aceita o XML "cru" da DPS assinada OU o envelope completo.
  Se o primeiro não der erro, vale testar o segundo para confirmar o
  cabeçalho SOAP.
- Este script usa certificado mTLS (o mesmo do .env) porque alguns endpoints
  do Nota Control exigem autenticação.
- Se o endpoint /validarxml aceitar só browser (não POST programático),
  o script falha com 405 Method Not Allowed — nesse caso, copie o XML e
  cole manualmente na URL indicada no final.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from loguru import logger

from src.config import settings
from src.nfse_df import _pkcs12_to_pem_tempfiles

URL_VALIDADOR = (
    "https://nfse.issnetonline.com.br/wsnfsenacional/homologacao/validarxml"
)


def _encontrar_xml_mais_recente(envelope: bool = False) -> Path | None:
    """Retorna o XML mais recente em `nfse_emitidas/`.

    Se envelope=True, busca arquivos `*_enviada_*.xml` (envelope SOAP);
    caso contrário, busca `*_dps_assinada_*.xml`.
    """
    padrao = "*_enviada_*.xml" if envelope else "*_dps_assinada_*.xml"
    candidatos = sorted(
        Path(settings.nfse_output_dir).glob(padrao),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidatos[0] if candidatos else None


def validar(xml_path: Path) -> dict:
    """Envia o XML para o validador online e retorna o resultado."""
    if not xml_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {xml_path}")

    xml_bytes = xml_path.read_bytes()
    logger.info(f"Validando {xml_path} ({len(xml_bytes)} bytes) em {URL_VALIDADOR}")

    # Extrai cert+key do .pfx para mTLS
    cert_path = Path(settings.nfse_certificado_path)
    senha = settings.nfse_certificado_senha
    cert_file, key_file = _pkcs12_to_pem_tempfiles(cert_path, senha)

    try:
        with httpx.Client(
            cert=(str(cert_file), str(key_file)),
            timeout=60.0,
            verify=True,
        ) as client:
            # Tenta POST com content-type XML
            response = client.post(
                URL_VALIDADOR,
                content=xml_bytes,
                headers={"Content-Type": "application/xml; charset=utf-8"},
            )

        return {
            "status_code": response.status_code,
            "response_headers": dict(response.headers),
            "response_body": response.text,
        }
    finally:
        for f in (cert_file, key_file):
            try:
                if f and f.exists():
                    f.unlink()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "xml",
        nargs="?",
        type=Path,
        default=None,
        help="Caminho do XML de DPS a validar",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Usa o XML mais recente em nfse_emitidas/ automaticamente",
    )
    parser.add_argument(
        "--envelope",
        action="store_true",
        help="Valida o envelope SOAP completo (em vez de só a DPS assinada)",
    )
    args = parser.parse_args()

    # Determina qual arquivo validar
    if args.latest:
        xml_path = _encontrar_xml_mais_recente(envelope=args.envelope)
        if not xml_path:
            print("❌ Nenhum XML encontrado em nfse_emitidas/")
            sys.exit(1)
    elif args.xml:
        xml_path = args.xml
    else:
        parser.error("Informe o caminho do XML ou use --latest")

    print(f"\n📄 XML alvo: {xml_path}")
    print(f"🌐 Endpoint validador: {URL_VALIDADOR}\n")

    try:
        resultado = validar(xml_path)
    except Exception as exc:
        logger.exception("Falha na validação")
        print(f"\n❌ ERRO: {exc}")
        print("\nAlternativa manual: abra no navegador e cole o XML:")
        print(f"    {URL_VALIDADOR}")
        sys.exit(1)

    sc = resultado["status_code"]
    body = resultado["response_body"]

    print(f"{'=' * 60}")
    print(f"📊 RESPOSTA HTTP {sc}")
    print(f"{'=' * 60}\n")

    if sc == 405:
        print(
            "⚠️  O endpoint não aceita POST programático.\n"
            "    Use o validador via navegador:"
        )
        print(f"    {URL_VALIDADOR}")
        print("\n    Cole o conteúdo do arquivo:")
        print(f"    {xml_path}")
    elif sc == 200:
        # Tenta detectar se é uma resposta de sucesso ou de lista de erros
        if any(
            x in body
            for x in ("MensagemRetorno", "ListaMensagem", "cStat>6", "E160", "E183")
        ):
            print("❌ XML COM ERROS:")
        else:
            print("✅ XML APROVADO (sem erros detectados):")
        print(body[:3000])
        if len(body) > 3000:
            print(f"\n... ({len(body) - 3000} chars truncados)")
    else:
        print(f"Status inesperado {sc}. Corpo:")
        print(body[:2000])


if __name__ == "__main__":
    main()
