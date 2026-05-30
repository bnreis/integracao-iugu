"""
Script de comparacao: Planilha x Iugu
Identifica empresas que estao na planilha mas NAO na Iugu (e vice-versa).

Uso:
  cd "C:\\Users\\bruno.reis\\.claude\\Workspace\\Integracao Iugo"
  .\\.venv\\Scripts\\Activate.ps1
  python comparar_planilha_iugu.py
"""
import sys
sys.path.insert(0, r"C:\Users\bruno.reis\.claude\Workspace\Integração Iugo")

from src.config import settings
from src.iugu_client import IuguClient
from src.spreadsheet import EmpresasRepository


def main():
    print("=" * 70)
    print("  COMPARACAO: Planilha x Iugu")
    print("=" * 70)

    # 1. Carrega planilha
    print("\nCarregando planilha...")
    repo = EmpresasRepository()
    repo.carregar(forcar=True)
    empresas_planilha = {cnpj: emp for cnpj, emp in repo._empresas.items()}
    print(f"   {len(empresas_planilha)} empresas na planilha")

    # 2. Carrega TODOS os customers da Iugu
    print("\nCarregando customers da Iugu...")
    all_customers = []
    with IuguClient() as client:
        start = 0
        while True:
            result = client.list_customers(limit=100, start=start)
            items = result.get("items", [])
            if not items:
                break
            all_customers.extend(items)
            total = result.get("totalItems", 0)
            start += len(items)
            if start >= total:
                break

    print(f"   {len(all_customers)} customers na Iugu")

    # Monta mapa CNPJ -> customer da Iugu
    iugu_por_cnpj = {}
    iugu_sem_cnpj = []
    for cust in all_customers:
        cpf_cnpj = cust.get("cpf_cnpj") or ""
        cnpj_digits = "".join(filter(str.isdigit, cpf_cnpj))
        if len(cnpj_digits) == 14:
            iugu_por_cnpj[cnpj_digits] = cust
        elif len(cnpj_digits) == 11:
            pass
        else:
            iugu_sem_cnpj.append(cust)

    print(f"   {len(iugu_por_cnpj)} com CNPJ valido, {len(iugu_sem_cnpj)} sem CNPJ/CPF")

    # 3. Comparacao
    cnpjs_planilha = set(empresas_planilha.keys())
    cnpjs_iugu = set(iugu_por_cnpj.keys())

    so_planilha = cnpjs_planilha - cnpjs_iugu
    so_iugu = cnpjs_iugu - cnpjs_planilha
    em_ambos = cnpjs_planilha & cnpjs_iugu

    print("\n" + "=" * 70)
    print(f"  EM AMBOS (planilha + Iugu): {len(em_ambos)}")
    print("=" * 70)
    for cnpj in sorted(em_ambos):
        emp = empresas_planilha[cnpj]
        cust = iugu_por_cnpj[cnpj]
        tem_endereco = bool(cust.get("zip_code") or cust.get("street"))
        endereco_str = "COM endereco" if tem_endereco else "SEM endereco"
        print(f"   {cnpj} | {emp.razao_social[:40]:<40} | {endereco_str}")

    if so_planilha:
        print("\n" + "=" * 70)
        print(f"  SO NA PLANILHA (nao existe na Iugu): {len(so_planilha)}")
        print("=" * 70)
        for cnpj in sorted(so_planilha):
            emp = empresas_planilha[cnpj]
            print(f"   {cnpj} | {emp.razao_social} | {emp.email}")
    else:
        print("\n  Todas as empresas da planilha existem na Iugu!")

    if so_iugu:
        print("\n" + "=" * 70)
        print(f"  SO NA IUGU (nao esta na planilha): {len(so_iugu)}")
        print("=" * 70)
        for cnpj in sorted(so_iugu):
            cust = iugu_por_cnpj[cnpj]
            print(f"   {cnpj} | {cust.get('name', '?')[:40]:<40} | {cust.get('email', '?')}")

    if iugu_sem_cnpj:
        print(f"\n  Customers Iugu sem CNPJ: {len(iugu_sem_cnpj)}")
        for cust in iugu_sem_cnpj[:5]:
            print(f"      id={cust.get('id','?')[:12]} | {cust.get('name', '?')[:30]} | {cust.get('email','?')}")
        if len(iugu_sem_cnpj) > 5:
            print(f"      ... e mais {len(iugu_sem_cnpj) - 5}")

    print("\n" + "=" * 70)
    print("  RESUMO PARA MIGRACAO")
    print("=" * 70)
    print(f"  Planilha: {len(cnpjs_planilha)} empresas")
    print(f"  Iugu:     {len(cnpjs_iugu)} customers com CNPJ")
    print(f"  Match:    {len(em_ambos)}")
    print(f"  So planilha (PRECISAM ser criadas na Iugu): {len(so_planilha)}")
    print(f"  So Iugu (podem ser ignoradas ou importadas): {len(so_iugu)}")
    print()
    if so_planilha:
        print("  ACAO NECESSARIA: Criar os customers faltantes na Iugu")
        print("  antes de desativar a planilha!")
    else:
        print("  Migracao segura - todos os CNPJs da planilha existem na Iugu.")


if __name__ == "__main__":
    main()
