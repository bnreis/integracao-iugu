"""Gera a DANFSE (PDF) a partir de um XML de NFS-e — para VALIDAR o layout.

Uso:
    python scripts/gerar_danfse_teste.py <nota.xml> [saida.pdf]

Ex.:
    python scripts/gerar_danfse_teste.py "0781513100130_...NFSe_00000042.xml" danfse_42.pdf

Depois abra o PDF e compare lado a lado com o oficial. Ajustamos o layout iterando.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.danfse_pdf import dados_de_consulta_xml, gerar_danfse_pdf  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: python scripts/gerar_danfse_teste.py <nota.xml> [saida.pdf]")
        return 2
    xml = Path(sys.argv[1]).read_text(encoding="utf-8")
    saida = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("danfse_teste.pdf")

    dados = dados_de_consulta_xml(xml)
    print("=== Campos extraídos do XML ===")
    for k in (
        "numero_nfse", "codigo_verificacao", "data_geracao", "data_competencia",
        "prest_razao", "prest_im", "prest_cnpj",
        "tom_razao", "tom_cnpj", "tom_im", "tom_email",
        "aliquota", "valor_servicos", "valor_iss", "valor_liquido", "iss_retido",
        "descricao",
    ):
        print(f"  {k:22}: {dados.get(k)}")

    pdf = gerar_danfse_pdf(dados)
    saida.write_bytes(pdf)
    print(f"\nPDF gerado: {saida.resolve()} ({len(pdf)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
