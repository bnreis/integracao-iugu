"""Compara os clientes da planilha com os customers cadastrados na Iugu
e reporta quais estão sem endereço ou ausentes da Iugu.

Uso:
    python scripts/auditar_enderecos_iugu.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguClient
from src.spreadsheet import EmpresasRepository


CAMPOS_ENDERECO = ("zip_code", "street", "number", "district", "city", "state")


def _so_digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _endereco_completo(cust: dict) -> bool:
    return all(cust.get(k) for k in CAMPOS_ENDERECO)


def _endereco_parcial(cust: dict) -> bool:
    return any(cust.get(k) for k in CAMPOS_ENDERECO) and not _endereco_completo(cust)


def _campos_faltando(cust: dict) -> list[str]:
    return [k for k in CAMPOS_ENDERECO if not cust.get(k)]


def _listar_todos_customers(client: IuguClient) -> list[dict]:
    todos = []
    start = 0
    step = 100
    while True:
        resp = client._request(
            "GET", "/v1/customers", params={"limit": step, "start": start}
        )
        items = resp.get("items") or []
        if not items:
            break
        todos.extend(items)
        if len(items) < step:
            break
        start += step
    return todos


def main():
    repo = EmpresasRepository()
    repo.carregar()
    empresas = repo.listar_ativas()
    print(f"Planilha: {len(empresas)} empresa(s) ativa(s)\n")

    with IuguClient() as c:
        customers = _listar_todos_customers(c)
    print(f"Iugu:     {len(customers)} customer(s) na conta\n")

    # Agrupa customers por CNPJ
    por_cnpj: dict[str, list[dict]] = {}
    for cust in customers:
        cnpj = _so_digitos(cust.get("cpf_cnpj"))
        if cnpj:
            por_cnpj.setdefault(cnpj, []).append(cust)

    ok: list[tuple] = []
    sem_endereco: list[tuple] = []
    parcial: list[tuple] = []
    ausente: list[tuple] = []
    duplicatas: list[tuple] = []

    for emp in empresas:
        cnpj = _so_digitos(emp.cnpj)
        lista = por_cnpj.get(cnpj, [])

        if not lista:
            ausente.append((emp, None))
            continue

        if len(lista) > 1:
            duplicatas.append((emp, lista))

        # Escolhe o melhor: com endereço completo > com parcial > sem
        melhor = None
        for cust in lista:
            if _endereco_completo(cust):
                melhor = cust
                break
        if melhor is None:
            for cust in lista:
                if _endereco_parcial(cust):
                    melhor = cust
                    break
        if melhor is None:
            melhor = lista[0]

        if _endereco_completo(melhor):
            ok.append((emp, melhor))
        elif _endereco_parcial(melhor):
            parcial.append((emp, melhor))
        else:
            sem_endereco.append((emp, melhor))

    bar = "=" * 70
    print(bar)
    print(f"✅ COM ENDEREÇO COMPLETO ({len(ok)})")
    print(bar)
    for emp, cust in ok:
        print(f"  {emp.cnpj}  {emp.razao_social}")

    print()
    print(bar)
    print(f"⚠️  COM ENDEREÇO PARCIAL — COMPLETAR CAMPOS ({len(parcial)})")
    print(bar)
    for emp, cust in parcial:
        faltam = _campos_faltando(cust)
        print(f"  {emp.cnpj}  {emp.razao_social}")
        print(f"    customer_id: {cust.get('id')}")
        print(f"    faltam: {', '.join(faltam)}")

    print()
    print(bar)
    print(f"❌ SEM ENDEREÇO NA IUGU — CADASTRAR TUDO ({len(sem_endereco)})")
    print(bar)
    for emp, cust in sem_endereco:
        print(f"  {emp.cnpj}  {emp.razao_social}")
        print(f"    customer_id: {cust.get('id')}")

    print()
    print(bar)
    print(f"🚫 NÃO EXISTEM NA IUGU — CRIAR CUSTOMER ({len(ausente)})")
    print(bar)
    for emp, _ in ausente:
        print(f"  {emp.cnpj}  {emp.razao_social}")

    if duplicatas:
        print()
        print(bar)
        print(f"🔀 CNPJs DUPLICADOS NA IUGU ({len(duplicatas)})")
        print(bar)
        for emp, lista in duplicatas:
            print(f"  {emp.cnpj}  {emp.razao_social} — {len(lista)} customers:")
            for cust in lista:
                status = "completo" if _endereco_completo(cust) else (
                    "parcial" if _endereco_parcial(cust) else "vazio"
                )
                print(f"    {cust.get('id')}  name={cust.get('name')!r}  endereço: {status}")

    print()
    print(bar)
    print(f"Resumo: {len(ok)} OK | {len(parcial)} parcial | {len(sem_endereco)} sem endereço | {len(ausente)} ausente")
    print(bar)


if __name__ == "__main__":
    main()
