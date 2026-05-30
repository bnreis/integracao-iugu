"""Inspeciona campos de endereço/customer de uma fatura da Iugu.

Uso:
    python scripts/inspecionar_fatura.py <invoice_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguClient


def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/inspecionar_fatura.py <invoice_id>")
        sys.exit(1)

    invoice_id = sys.argv[1]
    with IuguClient() as c:
        inv = c.get_invoice(invoice_id)

    print(f"status:          {inv.get('status')!r}")
    print(f"customer_id:     {inv.get('customer_id')!r}")
    print(f"payer_name:      {inv.get('payer_name')!r}")
    print(f"payer_cpf_cnpj:  {inv.get('payer_cpf_cnpj')!r}")
    print()
    print("--- campos com 'address' ou 'zip' ---")
    for k in sorted(inv.keys()):
        if "address" in k.lower() or "zip" in k.lower():
            print(f"  {k}: {inv[k]!r}")

    payer = inv.get("payer")
    if payer:
        print()
        print("--- payer (objeto aninhado, se existir) ---")
        if isinstance(payer, dict):
            for k, v in payer.items():
                print(f"  {k}: {v!r}")
        else:
            print(f"  payer = {payer!r}")


if __name__ == "__main__":
    main()
