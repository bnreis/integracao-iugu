"""
Script diário que gera os boletos recorrentes na Iugu.

Este script é agendado para rodar uma vez por dia (pela manhã).
Ele consulta a planilha de empresas e cria boletos para quem tem o
dia_criacao_fatura igual ao dia de hoje.

Uso:
    # Produção (rodar hoje)
    python scripts/run_scheduled_invoices.py

    # Dry-run (não chama a API, só mostra o que faria)
    python scripts/run_scheduled_invoices.py --dry-run

    # Reprocessar um dia específico (CUIDADO: pode duplicar boletos!)
    python scripts/run_scheduled_invoices.py --data 2026-04-10

    # Salvar resultado em JSON para auditoria
    python scripts/run_scheduled_invoices.py --saida-json logs/diario_2026-04-18.json

Agendamento no Windows Task Scheduler:
  Ver docs/scheduling.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger

from src.scheduled_invoices import executar_dia


def parse_data(valor: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Data inválida (use YYYY-MM-DD): {valor}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--data",
        type=parse_data,
        default=None,
        help="Data de referência YYYY-MM-DD (default: hoje)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--saida-json",
        type=Path,
        default=None,
        help="Arquivo JSON com resultado detalhado (opcional)",
    )
    args = parser.parse_args()

    data_ref = args.data or date.today()
    logger.info(f"Iniciando lote de {data_ref} (dry_run={args.dry_run})")

    lote = executar_dia(data_ref=data_ref, dry_run=args.dry_run)

    print(f"\n{'=' * 60}")
    print(f"📅 Relatório do lote — {data_ref}")
    print(f"{'=' * 60}")
    print(f"Empresas elegíveis hoje: {lote.total_empresas_elegiveis}")
    print(f"Sucessos:                {len(lote.sucessos)}")
    print(f"Falhas:                  {len(lote.falhas)}")
    if lote.ignoradas:
        print(f"Ignoradas (dry-run):     {len(lote.ignoradas)}")

    if lote.sucessos:
        print("\n✅ Boletos criados:")
        for r in lote.sucessos:
            print(f"   • {r.razao_social:<40} R$ {r.to_dict()['valor']}")
            print(f"     {r.secure_url or '—'}")

    if lote.falhas:
        print("\n❌ Falhas:")
        for r in lote.falhas:
            print(f"   • {r.razao_social:<40} → {r.erro}")

    if args.saida_json:
        args.saida_json.parent.mkdir(parents=True, exist_ok=True)
        args.saida_json.write_text(
            json.dumps(lote.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Resultado salvo em {args.saida_json}")

    # Exit code informa erro para o agendador (útil para notificações)
    if lote.falhas:
        sys.exit(2)


if __name__ == "__main__":
    main()
