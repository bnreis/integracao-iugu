"""
Semeia o registro local de customer_ids a partir das FATURAS da Iugu.

Contorno para a listagem /v1/customers quebrada (devolve só 1 cliente). O GET por
ID funciona, então: varremos TODAS as faturas (que listam normalmente), coletamos
os customer_id distintos, buscamos cada um por ID e guardamos APENAS os válidos
(HTTP 200) no registro (nfse_emitidas/registro_customer_ids.json). Os IDs de
clientes antigos/recriados (404) são descartados.

Roda OFFLINE (script CLI) — sem travar o webhook/app. Pode demorar (varre todas as
páginas de faturas + 1 GET por cliente distinto).

Uso (na VPS ou na máquina do Bruno, com .env válido):
    python scripts/seed_customer_ids.py
    python scripts/seed_customer_ids.py --max-paginas 30
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguClient, IuguAPIError  # noqa: E402
from src.iugu_empresas import (  # noqa: E402
    _ler_registro_customer_ids,
    _salvar_registro_customer_ids,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Semeia o registro de customer_ids pelas faturas.")
    parser.add_argument("--max-paginas", type=int, default=50, help="Máx. de páginas de faturas (100/página)")
    args = parser.parse_args()

    print("[seed] Coletando customer_ids das faturas...")
    ids: set[str] = set()
    with IuguClient() as client:
        start = 0
        paginas = 0
        while paginas < args.max_paginas:
            inv = client.list_invoices(limit=100, start=start)
            items = inv.get("items", [])
            if not items:
                break
            for it in items:
                cid = it.get("customer_id")
                if cid:
                    ids.add(cid)
            total = inv.get("totalItems", 0)
            start += len(items)
            paginas += 1
            print(f"  página {paginas}: +{len(items)} faturas (acum. {start}/{total}) | ids distintos: {len(ids)}")
            if start >= total:
                break

        print(f"[seed] {len(ids)} customer_ids distintos. Validando por ID (mantém só os 200)...")

        validos: set[str] = set()

        def _check(cid: str) -> tuple[str, bool]:
            try:
                client.get_customer(cid)
                return cid, True
            except IuguAPIError as e:
                if getattr(e, "status_code", None) == 404:
                    return cid, False
                # erro não-404: mantém (não descarta por falha transitória)
                return cid, True
            except Exception:
                return cid, True

        with ThreadPoolExecutor(max_workers=8) as ex:
            for cid, ok in ex.map(_check, list(ids)):
                if ok:
                    validos.add(cid)

    # Une com o registro existente (não perde nada já conhecido).
    final = validos | _ler_registro_customer_ids()
    _salvar_registro_customer_ids(final)

    print(f"[seed] Concluído. {len(validos)} válidos das faturas; registro final: {len(final)} customer_ids.")
    print("[seed] Reinicie o serviço (systemctl restart iugu-webhook) para o cache recarregar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
