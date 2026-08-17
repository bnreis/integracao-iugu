"""Mostra os valores que serão usados na FATURA (boleto Iugu) e na NFS-e para uma
empresa, lendo o CADASTRO REAL da Iugu. Não cria nada — só simula/valida.

    python scripts/simular_valores_fatura.py            # default: busca "fipecq"
    python scripts/simular_valores_fatura.py --busca almeria
    python scripts/simular_valores_fatura.py --busca 36342291000143

Regra (ISS retido): boleto cobra o LÍQUIDO (bruto - ISS retido); a NFS-e usa o
BRUTO (valor cheio do serviço). Sem retenção, os dois são iguais ao bruto.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_empresas import format_cents_to_br, get_repo  # noqa: E402


def _casa(emp, termo: str) -> bool:
    t = termo.lower().strip()
    alvo = f"{emp.razao_social} {emp.cnpj}".lower()
    return t in alvo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--busca", default="fipecq", help="termo (razão social ou CNPJ)")
    args = ap.parse_args()

    repo = get_repo(forcar=True)  # recarrega da Iugu (dados frescos)
    achados = [e for e in repo.listar_ativas() if _casa(e, args.busca)]

    if not achados:
        print(f"Nenhuma empresa ativa casando com '{args.busca}'.")
        return 1

    for emp in achados:
        bruto = emp.valor_fatura_cents
        liquido = emp.valor_cobranca_cents
        retencao = bruto - liquido
        print("=" * 66)
        print(f"Empresa       : {emp.razao_social}")
        print(f"CNPJ          : {emp.cnpj}   customer_id: {emp.customer_id}")
        print(f"Emite NF-e    : {emp.emitir_nf}   ISS retido: {emp.iss_retido}")
        print(f"Cód. serviço  : {emp.codigo_servico!r}   Alíquota ISS: {emp.aliquota_iss}%")
        print(f"Descr. serviço: {emp.descricao_servico!r}")
        print(f"Insc. Municip.: {emp.inscricao_municipal or '(vazia)'}")
        print(f"valor_fatura  : '{emp.valor_fatura}'  |  dia criação: {emp.dia_criacao_fatura}")
        print("-" * 66)
        print(f"  Valor do serviço (BRUTO / vai na NFS-e) : R$ {format_cents_to_br(bruto)}")
        if emp.iss_retido and emp.aliquota_iss:
            print(f"  ISS retido na fonte ({emp.aliquota_iss}%)          : R$ {format_cents_to_br(retencao)}")
        print(f"  >> VALOR DA FATURA / boleto Iugu        : R$ {format_cents_to_br(liquido)}")
        if emp.iss_retido and emp.aliquota_iss:
            print(f"  >> NFS-e: valor do serviço  = R$ {format_cents_to_br(bruto)} (bruto)")
            print(f"           líquido a receber = R$ {format_cents_to_br(liquido)} (bruto - ISS)")
        else:
            print("  (sem ISS retido: fatura e NFS-e usam o mesmo valor bruto)")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
