"""
Script de migracao: grava dados de negocio no campo 'notes' (JSON)
dos customers da Iugu.

Para cada empresa na planilha, encontra o customer correspondente na Iugu
(por CNPJ) e grava os campos de negocio como JSON no campo notes:
  - codigo_servico, descricao_servico, aliquota_iss
  - emitir_nf, nf_na_criacao
  - descricao_boleto, valor_fatura, dia_criacao_fatura
  - ativo, observacoes

Uso:
  python migrar_custom_variables.py --dry-run
  python migrar_custom_variables.py
"""
import argparse
import json
import sys
import time

sys.path.insert(0, r"C:\Users\bruno.reis\.claude\Workspace\Integração Iugo")

from src.config import settings
from src.iugu_client import IuguClient, IuguAPIError
from src.spreadsheet import EmpresasRepository


def _montar_notes_json(emp):
    """Monta o JSON com dados de negocio para gravar no campo notes."""
    dados = {
        "codigo_servico": str(emp.codigo_servico or ""),
        "descricao_servico": str(emp.descricao_servico or ""),
        "aliquota_iss": float(emp.aliquota_iss),
        "emitir_nf": bool(emp.emitir_nf),
        "nf_na_criacao": bool(emp.nf_na_criacao),
        "descricao_boleto": str(emp.descricao_boleto or ""),
        "valor_fatura": str(emp.valor_fatura or ""),
        "dia_criacao_fatura": int(emp.dia_criacao_fatura),
        "ativo": bool(emp.ativo),
        "observacoes": str(emp.observacoes or ""),
    }
    return json.dumps(dados, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Migra dados da planilha para notes (JSON) na Iugu")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar na Iugu")
    args = parser.parse_args()

    print("=" * 70)
    print("  MIGRACAO: Planilha -> campo notes (JSON) na Iugu")
    if args.dry_run:
        print("  [MODO DRY-RUN -- nenhuma alteracao sera feita]")
    print("=" * 70)

    # 1. Carrega planilha
    print("\n1. Carregando planilha...")
    repo = EmpresasRepository()
    repo.carregar(forcar=True)
    empresas = list(repo._empresas.values())
    print(f"   {len(empresas)} empresas na planilha")

    # 2. Carrega todos os customers da Iugu e monta mapa CNPJ -> customer
    print("\n2. Carregando customers da Iugu...")
    iugu_por_cnpj = {}
    with IuguClient() as client:
        start = 0
        while True:
            result = client.list_customers(limit=100, start=start)
            items = result.get("items", [])
            if not items:
                break
            for cust in items:
                cpf_cnpj = cust.get("cpf_cnpj") or ""
                cnpj_digits = "".join(filter(str.isdigit, cpf_cnpj))
                if len(cnpj_digits) == 14:
                    iugu_por_cnpj[cnpj_digits] = cust
            total = result.get("totalItems", 0)
            start += len(items)
            if start >= total:
                break
    print(f"   {len(iugu_por_cnpj)} customers com CNPJ na Iugu")

    # 3. Migracao
    print("\n3. Migrando dados para campo notes (JSON)...")
    print("-" * 70)

    sucesso = 0
    falhas = 0
    nao_encontrados = 0

    with IuguClient() as client:
        for emp in empresas:
            customer = iugu_por_cnpj.get(emp.cnpj)
            if not customer:
                print(f"   NAO ENCONTRADO na Iugu: {emp.cnpj} | {emp.razao_social}")
                nao_encontrados += 1
                continue

            cust_id = customer["id"]
            notes_json = _montar_notes_json(emp)

            if args.dry_run:
                print(f"   [DRY] {emp.cnpj} | {emp.razao_social[:40]:<40}")
                dados = json.loads(notes_json)
                for k, v in dados.items():
                    print(f"         {k}: {v}")
                sucesso += 1
            else:
                try:
                    client.update_customer(cust_id, notes=notes_json)
                    print(f"   OK {emp.cnpj} | {emp.razao_social[:40]:<40}")
                    sucesso += 1
                    time.sleep(0.3)
                except IuguAPIError as e:
                    print(f"   ERRO {emp.cnpj} | {emp.razao_social[:40]:<40} | {e.message}")
                    falhas += 1

    # 4. Resumo
    print("\n" + "=" * 70)
    print("  RESUMO DA MIGRACAO")
    print("=" * 70)
    print(f"  Total na planilha:    {len(empresas)}")
    print(f"  Migrados com sucesso: {sucesso}")
    print(f"  Falhas:               {falhas}")
    print(f"  Nao encontrados:      {nao_encontrados}")

    if nao_encontrados > 0:
        print(f"\n  AVISO: {nao_encontrados} empresa(s) da planilha NAO existem na Iugu!")
        print("  Crie esses customers antes de desativar a planilha.")

    if falhas > 0:
        print(f"\n  AVISO: {falhas} falha(s) -- reexecute o script para tentar novamente.")

    if falhas == 0 and nao_encontrados == 0:
        print("\n  Migracao completa! Todos os dados foram gravados na Iugu.")
        if args.dry_run:
            print("     (modo dry-run -- execute sem --dry-run para gravar de verdade)")

    return 0 if (falhas == 0 and nao_encontrados == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
