"""
Consulta a URL oficial de uma NFS-e no ISSnet DF (operação ConsultarUrlNfse).

Operação EXCLUSIVA do ISSnet DF do webservice ABRASF 2.04 — obtém a URL de
visualização/impressão de uma NFS-e já emitida e, a partir dela, tenta baixar o
PDF/DANFSE oficial.

⚠️ Faz CHAMADA DE REDE real ao ISSnet (mTLS). Rode na máquina do Bruno (o sandbox
do Cowork não alcança df.issnetonline.com.br). O leiaute do envelope foi montado
com base no padrão ABRASF de consultas e PRECISA ser refinado pelo retorno real.

Uso (a partir da raiz do projeto):
    # consulta a NFS-e #408 (default) — força ambiente de produção
    python scripts/consultar_url_nfse.py
    python scripts/consultar_url_nfse.py 408
    python scripts/consultar_url_nfse.py 123
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Força produção ANTES de importar src.config (settings é lido na importação).
# A NFS-e #408 só existe no ambiente de produção do ISSnet.
os.environ.setdefault("NFSE_AMBIENTE", "producao")

from src.config import settings  # noqa: E402
from src.nfse_df import baixar_pdf_nfse, consultar_url_nfse  # noqa: E402


async def _executar(numero: str) -> None:
    # Garante produção mesmo se o .env trouxer outro valor (settings é mutável).
    if settings.nfse_ambiente != "producao":
        settings.nfse_ambiente = "producao"

    print(f"== ConsultarUrlNfse — NFS-e #{numero} (ambiente={settings.nfse_ambiente}) ==\n")

    res = await consultar_url_nfse(numero)

    print(f"sucesso   : {res['sucesso']}")
    print(f"url       : {res['url']}")
    print(f"mensagens : {res['mensagens']}")
    print("\n--- início do raw_response (estrutura real do retorno) ---")
    raw = res.get("raw_response") or ""
    print(raw[:3000] if raw else "(vazio)")
    print("--- fim do trecho do raw_response ---\n")

    url = res.get("url")
    if url:
        print(f"Tentando baixar o PDF de: {url}")
        pdf = baixar_pdf_nfse(url)
        if pdf is not None:
            destino = ROOT / "nfse_emitidas" / f"nfse_{numero}.pdf"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(pdf)
            print(f"PDF baixado: {len(pdf)} bytes → {destino}")
        else:
            print(
                "A URL NÃO devolveu um PDF direto (provavelmente é uma página HTML "
                "de visualização). Veja o content-type logado acima."
            )
    else:
        print("Sem URL no retorno — inspecione o raw_response e ajuste o parser/leiaute.")


def main() -> None:
    numero = sys.argv[1] if len(sys.argv) > 1 else "408"
    asyncio.run(_executar(numero))


if __name__ == "__main__":
    main()
