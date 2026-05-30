"""Lista os customers cadastrados na conta Iugu e mostra os campos de endereço.

Uso:
    python scripts/listar_customers_iugu.py
    python scripts/listar_customers_iugu.py --query 36342291000143
    python scripts/listar_customers_iugu.py --limit 20
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", help="Filtro por nome/email/CPF-CNPJ")
    parser.add_argument("--limit", type=int, default=10, help="Qtd máxima (default 10)")
    args = parser.parse_args()

    params = {"limit": args.limit}
    if args.query:
        params["query"] = args.query

    with IuguClient() as c:
        resp = c._request("GET", "/v1/customers", params=params)

    total = resp.get("totalItems", resp.get("total", "?"))
    items = resp.get("items") or []
    print(f"Total de customers na conta: {total}")
    print(f"Mostrando {len(items)} item(s):\n")

    for i, cust in enumerate(items, 1):
        print(f"--- Customer #{i} ---")
        print(f"  id:          {cust.get('id')}")
        print(f"  name:        {cust.get('name')!r}")
        print(f"  cpf_cnpj:    {cust.get('cpf_cnpj')!r}")
        print(f"  email:       {cust.get('email')!r}")
        print(f"  zip_code:    {cust.get('zip_code')!r}")
        print(f"  street:      {cust.get('street')!r}")
        print(f"  number:      {cust.get('number')!r}")
        print(f"  district:    {cust.get('district')!r}")
        print(f"  city:        {cust.get('city')!r}")
        print(f"  state:       {cust.get('state')!r}")
        print()


if __name__ == "__main__":
    main()
