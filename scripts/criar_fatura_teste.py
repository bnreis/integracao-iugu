"""
Cria uma fatura de teste de R$ 1,00 na Iugu para a Megasuporte.

Usado para validar o fluxo E2E:
  1. Este script cria a fatura (boleto + pix)
  2. Bruno paga a fatura
  3. A Iugu envia o webhook para o servidor
  4. O sistema recebe, busca na planilha e gera a NFS-e (dry-run)
  5. O e-mail de NFS-e é enviado (se dry-run retornar sucesso)

Uso:
    python scripts/criar_fatura_teste.py
    python scripts/criar_fatura_teste.py --valor 5.00
    python scripts/criar_fatura_teste.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.iugu_client import IuguClient, IuguAPIError


def main():
    parser = argparse.ArgumentParser(description="Cria fatura de teste na Iugu")
    parser.add_argument(
        "--valor", type=float, default=1.00,
        help="Valor em reais (default: 1.00)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Apenas mostra o payload sem criar a fatura",
    )
    args = parser.parse_args()

    valor_cents = int(args.valor * 100)
    due_date = date.today() + timedelta(days=10)

    payload = {
        "email": "bnreis@gmail.com",
        "due_date": due_date.isoformat(),
        "items": [{
            "description": "Serviço de TI - Teste E2E",
            "quantity": 1,
            "price_cents": valor_cents,
        }],
        "payer": {
            "cpf_cnpj": "36342291000143",
            "name": "MEGASUPORTE SERVIÇOS DE TI LTDA",
            "email": "bnreis@gmail.com",
        },
        "payable_with": ["bank_slip", "pix"],
        "expires_in": str(settings.fatura_dias_expiracao),
        "bank_slip_extra_due": str(settings.fatura_boleto_extra_due),
        "fines": settings.fatura_multa_atraso_percentual > 0,
        "late_payment_fine": int(settings.fatura_multa_atraso_percentual),
        "per_day_interest": settings.fatura_juros_por_dia,
        "ignore_due_email": settings.fatura_ignorar_email_vencimento,
        "custom_variables": [
            {"name": "origem", "value": "teste_e2e"},
            {"name": "tipo", "value": "fatura_teste_manual"},
            {"name": "cnpj_tomador", "value": "36342291000143"},
            {"name": "nfse_emitida_na_criacao", "value": "false"},
        ],
    }

    print("=" * 60)
    print("  CRIAR FATURA DE TESTE — IUGU")
    print("=" * 60)
    print()
    print(f"  Empresa:    MEGASUPORTE SERVIÇOS DE TI LTDA")
    print(f"  CNPJ:       36.342.291/0001-43")
    print(f"  Valor:      R$ {args.valor:.2f}")
    print(f"  Vencimento: {due_date.isoformat()}")
    print(f"  Pagamento:  boleto + pix")
    print(f"  E-mail:     bnreis@gmail.com")
    print(f"  NFS-e DRY-RUN: {settings.nfse_dry_run}")
    print()

    if args.dry_run:
        print("[DRY-RUN] Payload que seria enviado:")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        print()
        print("Nenhuma fatura foi criada.")
        return

    print("Criando fatura na Iugu...")
    print()

    try:
        with IuguClient() as client:
            invoice = client.create_invoice(**payload)
    except IuguAPIError as e:
        print(f"❌ Erro da API Iugu: [{e.status_code}] {e.message}")
        if e.errors:
            print(f"   Detalhes: {e.errors}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        sys.exit(1)

    invoice_id = invoice.get("id", "?")
    secure_url = invoice.get("secure_url", "")
    bank_slip = invoice.get("bank_slip") or {}
    digitable_line = bank_slip.get("digitable_line", "")
    pix = invoice.get("pix") or {}
    pix_qrcode = pix.get("qrcode_text", "")

    print("=" * 60)
    print("  ✅ FATURA CRIADA COM SUCESSO!")
    print("=" * 60)
    print()
    print(f"  ID:              {invoice_id}")
    print(f"  Status:          {invoice.get('status', '?')}")
    print(f"  URL de pagamento: {secure_url}")
    print()
    if digitable_line:
        print(f"  Boleto (linha digitável):")
        print(f"  {digitable_line}")
        print()
    if pix_qrcode:
        print(f"  Pix (copia e cola):")
        print(f"  {pix_qrcode}")
        print()
    print("=" * 60)
    print()
    print("PRÓXIMOS PASSOS:")
    print(f"  1. Certifique-se de que o servidor webhook está rodando")
    print(f"     (uvicorn src.webhook_server:app --host 0.0.0.0 --port 8000)")
    print(f"  2. Certifique-se de que o Cloudflared está ativo")
    print(f"  3. Pague a fatura usando o link acima (pix ou boleto)")
    print(f"  4. Acompanhe os logs do servidor para ver o webhook chegar")
    print(f"  5. O sistema vai:")
    print(f"     - Receber o webhook de pagamento")
    print(f"     - Buscar a fatura na API Iugu")
    print(f"     - Encontrar a Megasuporte na planilha")
    print(f"     - Gerar os dados da NFS-e (DRY-RUN)")
    print(f"     - Enviar e-mail com o resultado")
    print()
    print(f"  Para reprocessar manualmente:")
    print(f"  curl -X POST http://localhost:8000/processar/{invoice_id}")


if __name__ == "__main__":
    main()
