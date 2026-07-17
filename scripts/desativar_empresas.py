"""Desativa (congela) as empresas/clientes da conta Iugu CONFIGURADA no .env.

Caso de uso (migração MegaSuporte -> MegaTeam): parar a automação da MegaSuporte
para os clientes, sem apagar nada. Para cada cliente, seta no campo `notes`:
    - ativo = False              -> sai do cron (não cria novos boletos) e das listagens
    - dia_criacao_fatura = 0     -> reforça: sem cobrança recorrente
    - emitir_nf = False          -> NÃO emite NFS-e quando uma fatura for paga (o `ativo`
                                    sozinho NÃO bloqueia isso — o webhook checa emitir_nf)

⚠️ Age na conta do TOKEN do .env atual. Rode a partir de /opt/integracao-iugu
(MegaSuporte). Confere e ABORTA se a conta não bater com --esperado-cnpj (se informado).

O que ISTO garante (sim): sem NOVAS faturas automáticas (cron) e sem NFS-e ao pagar.
O que ISTO NÃO faz: não cancela boletos JÁ abertos na Iugu, e não bloqueia criação
MANUAL de fatura pelo painel (ação do operador). Para cancelar boletos abertos, é outro
passo (pelo painel/API).

    python scripts/desativar_empresas.py                         # DRY-RUN (só mostra)
    python scripts/desativar_empresas.py --executar              # aplica
    python scripts/desativar_empresas.py --esperado-cnpj 36342291000143 --executar
    python scripts/desativar_empresas.py --busca almeria         # só as que casam
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.iugu_client import IuguAPIError, IuguClient  # noqa: E402
from src.iugu_empresas import empresa_para_notes_json, get_repo, normalizar_cnpj  # noqa: E402


def _ja_congelada(emp) -> bool:
    return (not emp.ativo) and (not emp.emitir_nf) and int(emp.dia_criacao_fatura or 0) == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true", help="Aplica de verdade (senão, dry-run).")
    ap.add_argument("--busca", default="", help="Filtra por termo na razão social/CNPJ (vazio = TODAS).")
    ap.add_argument("--esperado-cnpj", default="", help="Aborta se o CNPJ do prestador do .env não bater (trava de segurança).")
    args = ap.parse_args()

    prestador = normalizar_cnpj(settings.nfse_cnpj_prestador or "")
    if args.esperado_cnpj and normalizar_cnpj(args.esperado_cnpj) != prestador:
        print(f"ABORTADO: conta do .env é o prestador {prestador}, diferente do esperado "
              f"{normalizar_cnpj(args.esperado_cnpj)}. Rode na instância certa.")
        return 2

    dry = not args.executar
    modo = "DRY-RUN (nada será alterado)" if dry else "EXECUÇÃO REAL (vai desativar)"
    print("=" * 70)
    print(f"DESATIVAR empresas da conta Iugu do PRESTADOR: {prestador or '(desconhecido)'}")
    print(f"Efeito por cliente: ativo=False, emitir_nf=False, dia_criacao_fatura=0")
    print(f"Modo: {modo}")
    print("=" * 70)

    repo = get_repo(forcar=True)
    empresas = list(repo._empresas.values())
    if args.busca:
        t = args.busca.lower()
        empresas = [e for e in empresas if t in f"{e.razao_social} {e.cnpj}".lower()]
    print(f"Clientes na conta: {len(empresas)}\n")

    alteradas = puladas = erros = 0
    client = None if dry else IuguClient()
    try:
        for emp in empresas:
            rotulo = f"{emp.razao_social[:38]:38} ({emp.cnpj})"
            if _ja_congelada(emp):
                print(f"  [JÁ DESATIVADA] {rotulo}")
                puladas += 1
                continue
            if not emp.customer_id:
                print(f"  [PULADO] {rotulo}: sem customer_id")
                puladas += 1
                continue
            if dry:
                print(f"  [DESATIVARIA] {rotulo}  (ativo {emp.ativo}->False, emitir_nf {emp.emitir_nf}->False, dia {emp.dia_criacao_fatura}->0)")
                alteradas += 1
                continue
            try:
                emp.ativo = False
                emp.emitir_nf = False
                emp.dia_criacao_fatura = 0
                client.update_customer(emp.customer_id, notes=empresa_para_notes_json(emp))
                print(f"  [DESATIVADA] {rotulo}")
                alteradas += 1
            except IuguAPIError as e:
                print(f"  [ERRO] {rotulo}: [{e.status_code}] {e.message}")
                erros += 1
    finally:
        if client:
            client.close()

    verbo = "desativaria" if dry else "desativadas"
    print(f"\nResumo: {alteradas} {verbo} | {puladas} já desativadas/sem id | {erros} erros")
    if dry:
        print("Foi DRY-RUN. Revise a lista e rode com --executar para aplicar.")
    else:
        print("Feito. Reinicie o serviço p/ limpar o cache do repositório: sudo systemctl restart iugu-webhook")
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
