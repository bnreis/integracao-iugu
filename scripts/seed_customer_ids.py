"""
Semeia o registro local de customer_ids via BUSCA na Iugu (query a-z).

Contorno para a listagem /v1/customers SEM filtro estar quebrada (devolve só 1).
Descobrimos que /v1/customers?query=<termo> FUNCIONA. Então enumeramos todos os
clientes buscando por cada letra/dígito (a-z, 0-9) e unimos os IDs retornados —
isso cobre praticamente qualquer nome/e-mail/CNPJ. Resultado gravado no registro
(nfse_emitidas/registro_customer_ids.json), que o carregar() usa.

Roda OFFLINE (CLI). Rápido (buscas em paralelo). Não altera nada na Iugu.

Uso (na VPS, com .env válido):
    python scripts/seed_customer_ids.py
"""
from __future__ import annotations

import string
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguClient  # noqa: E402
from src.iugu_empresas import (  # noqa: E402
    _ler_registro_customer_ids,
    _salvar_registro_customer_ids,
)


def main() -> int:
    # Termos de busca: letras + dígitos cobrem nomes, e-mails e CNPJs.
    termos = list(string.ascii_lowercase) + list(string.digits)
    print(f"[seed] Enumerando clientes por busca ({len(termos)} termos: a-z, 0-9)...")

    ids: set[str] = set()
    nomes: dict[str, str] = {}
    with IuguClient() as client:
        def _q(termo: str):
            try:
                r = client.list_customers(query=termo, limit=100)
                return [(i.get("id"), i.get("name") or "") for i in r.get("items", []) if i.get("id")]
            except Exception as e:  # noqa: BLE001
                print(f"  [aviso] busca '{termo}' falhou: {e}")
                return []

        with ThreadPoolExecutor(max_workers=8) as ex:
            for pares in ex.map(_q, termos):
                for cid, nome in pares:
                    ids.add(cid)
                    nomes[cid] = nome

    print(f"[seed] {len(ids)} clientes distintos encontrados por busca:")
    for cid in sorted(ids, key=lambda c: nomes.get(c, "")):
        print(f"   - {nomes.get(cid, '?')} | {cid}")

    final = ids | _ler_registro_customer_ids()
    _salvar_registro_customer_ids(final)
    print(f"[seed] Registro final: {len(final)} customer_ids.")
    print("[seed] Reinicie o serviço: systemctl restart iugu-webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
