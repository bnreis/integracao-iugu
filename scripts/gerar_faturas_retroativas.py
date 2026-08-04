"""Gera os boletos RETROATIVOS do mês corrente para a conta Iugu CONFIGURADA no .env.

Caso de uso (ADR-0007, migração p/ MegaTeam): o cron da MegaTeam não estava agendado,
então os clientes cujo `dia_criacao_fatura` JÁ passou este mês ficaram sem boleto. Este
script "acerta" o mês: cria o boleto de cada cliente elegível cujo dia já passou e que
AINDA NÃO tem fatura neste mês.

Elegível = recorrente (ativo + valor_fatura>0 + dia_criacao_fatura 1..31) E
           dia_efetivo(hoje, dia) <= hoje (o dia de cobrança já chegou/passou) E
           o cliente ainda NÃO tem fatura criada neste mês (dedup por customer_id e CNPJ).

⚠️ Cria SOMENTE o boleto — NÃO emite NFS-e (mesmo p/ nf_na_criacao=True). Coerente com a
decisão de validar a 1ª emissão da MegaTeam só na virada do mês. A NFS-e sai no pagamento
(via webhook) quando o gatilho estiver configurado.

Roda na conta do TOKEN do .env atual — use a trava --esperado-cnpj p/ garantir a MegaTeam.

    python scripts/gerar_faturas_retroativas.py --esperado-cnpj 27987745000142            # DRY-RUN
    python scripts/gerar_faturas_retroativas.py --esperado-cnpj 27987745000142 --executar  # cria
    python scripts/gerar_faturas_retroativas.py --busca almeria                            # filtra
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.iugu_client import IuguAPIError, IuguClient  # noqa: E402
from src.iugu_empresas import format_cents_to_br, get_repo, normalizar_cnpj  # noqa: E402
from src.scheduled_invoices import criar_boleto_para_empresa, dia_efetivo  # noqa: E402


def _faturados_no_mes(client: IuguClient, ano: int, mes: int) -> tuple[set, set]:
    """Retorna (customer_ids, cnpjs) que JÁ têm fatura criada neste mês (dedup)."""
    ultimo = calendar.monthrange(ano, mes)[1]
    ini = f"{ano:04d}-{mes:02d}-01T00:00:00-03:00"
    fim = f"{ano:04d}-{mes:02d}-{ultimo:02d}T23:59:59-03:00"
    cids: set = set()
    cnpjs: set = set()
    start = 0
    while True:
        res = client.list_invoices(limit=100, start=start, created_at_from=ini, created_at_to=fim)
        items = res.get("items", []) or []
        for it in items:
            if it.get("customer_id"):
                cids.add(it["customer_id"])
            doc = normalizar_cnpj(str(it.get("payer_cpf_cnpj") or ""))
            if doc:
                cnpjs.add(doc)
        if len(items) < 100:
            break
        start += 100
    return cids, cnpjs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true", help="Cria de verdade (senão, dry-run).")
    ap.add_argument("--busca", default="", help="Filtra por termo na razão social/CNPJ.")
    ap.add_argument("--esperado-cnpj", default="", help="Aborta se o prestador do .env não bater (trava).")
    args = ap.parse_args()

    prestador = normalizar_cnpj(settings.nfse_cnpj_prestador or "")
    if args.esperado_cnpj and normalizar_cnpj(args.esperado_cnpj) != prestador:
        print(f"ABORTADO: conta do .env é o prestador {prestador}, diferente do esperado "
              f"{normalizar_cnpj(args.esperado_cnpj)}. Rode na instância certa.")
        return 2

    hoje = date.today()
    dry = not args.executar
    print("=" * 72)
    print(f"CATCH-UP de boletos — prestador {prestador or '(?)'} — mês {hoje.year}-{hoje.month:02d}")
    print(f"Regra: cria só onde o dia de cobrança JÁ passou (<= dia {hoje.day}) e ainda não há fatura no mês.")
    print(f"NFS-e: NÃO emite (só boleto). Modo: {'DRY-RUN' if dry else 'EXECUÇÃO REAL'}")
    print("=" * 72)

    repo = get_repo(forcar=True)
    recorrentes = repo.empresas_com_boleto_recorrente()
    if args.busca:
        t = args.busca.lower()
        recorrentes = [e for e in recorrentes if t in f"{e.razao_social} {e.cnpj}".lower()]
    print(f"Clientes recorrentes (ativo + valor + dia): {len(recorrentes)}\n")

    criados = pulados = erros = 0
    with IuguClient() as client:
        cids_mes, cnpjs_mes = _faturados_no_mes(client, hoje.year, hoje.month)
        for emp in recorrentes:
            rot = f"{emp.razao_social[:34]:34} ({emp.cnpj})"
            dia_alvo = dia_efetivo(hoje, emp.dia_criacao_fatura)
            if dia_alvo == 0:
                print(f"  [PULA] {rot}: dia_criacao_fatura inválido"); pulados += 1; continue
            if dia_alvo > hoje.day:
                print(f"  [PULA] {rot}: dia {dia_alvo} ainda não chegou (o cron pega no dia)"); pulados += 1; continue
            if (emp.customer_id and emp.customer_id in cids_mes) or normalizar_cnpj(emp.cnpj) in cnpjs_mes:
                print(f"  [JÁ TEM] {rot}: já existe fatura neste mês"); pulados += 1; continue

            if dry:
                print(f"  [CRIARIA] {rot}  R$ {format_cents_to_br(emp.valor_cobranca_cents)} (dia {dia_alvo})")
                criados += 1
                continue
            try:
                r = criar_boleto_para_empresa(emp, hoje, client=client)
                if r.sucesso:
                    print(f"  [CRIADO] {rot} -> {r.invoice_id}  R$ {format_cents_to_br(r.valor_cents)}")
                    criados += 1
                else:
                    print(f"  [ERRO] {rot}: {r.erro}"); erros += 1
            except IuguAPIError as e:
                print(f"  [ERRO] {rot}: [{e.status_code}] {e.message}"); erros += 1

    verbo = "criaria" if dry else "criados"
    print(f"\nResumo: {criados} {verbo} | {pulados} pulados | {erros} erros")
    if dry:
        print("Foi DRY-RUN. Revise a lista e rode com --executar para criar de verdade.")
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
