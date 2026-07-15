"""Importa (copia) os clientes de faturamento de UMA conta Iugu para OUTRA.

Caso de uso (ADR-0007): replicar o cadastro de clientes da MegaSuporte (conta atual,
token no .env) para a conta Iugu da MegaTeam, para que as duas tenham o mesmo cadastro
(dados + `notes` de negócio: codigo_servico, aliquota_iss, iss_retido, IM do tomador...).

ORIGEM  = conta configurada no .env (settings.iugu_api_token) — a MegaSuporte.
DESTINO = conta informada em --destino-token — a MegaTeam.

Idempotente: antes de criar, procura o CNPJ na conta destino (busca por query, que
funciona mesmo com o bug de listagem da Iugu) e PULA se já existir.

SEGURANÇA: passe o token destino por variável de ambiente, não na linha de comando
(evita vazar no histórico do shell):
    export IUGU_DESTINO_TOKEN=...           # (no shell da máquina, não no chat)
    python scripts/importar_clientes_entre_contas.py                 # dry-run
    python scripts/importar_clientes_entre_contas.py --executar      # cria de verdade

Ou explicitamente: --destino-token <token>  (menos seguro).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iugu_client import IuguAPIError, IuguClient  # noqa: E402
from src.iugu_empresas import get_repo, normalizar_cnpj  # noqa: E402

# Campos do customer Iugu que copiamos (identidade + endereço + notes de negócio).
# custom_variables NÃO são copiadas (metadados específicos da conta de origem).
_CAMPOS_ENDERECO = ("zip_code", "number", "street", "city", "state", "district", "complement")


def _ja_existe_no_destino(dest: IuguClient, cnpj_digits: str) -> bool:
    """True se já há um customer com este CNPJ na conta destino (dedup idempotente)."""
    try:
        res = dest.list_customers(query=cnpj_digits, limit=100)
    except IuguAPIError:
        return False
    for item in res.get("items", []) or []:
        if normalizar_cnpj(str(item.get("cpf_cnpj") or "")) == cnpj_digits:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--destino-token",
        default=os.environ.get("IUGU_DESTINO_TOKEN", ""),
        help="Token da API Iugu da conta DESTINO (MegaTeam). Prefira a env IUGU_DESTINO_TOKEN.",
    )
    ap.add_argument(
        "--executar",
        action="store_true",
        help="Cria de verdade. Sem esta flag, roda em DRY-RUN (só mostra o que faria).",
    )
    args = ap.parse_args()

    if not args.destino_token:
        print("ERRO: informe o token destino via env IUGU_DESTINO_TOKEN ou --destino-token.")
        return 2

    dry = not args.executar
    modo = "DRY-RUN (nada será criado)" if dry else "EXECUÇÃO REAL (vai criar customers)"
    print(f"== Importar clientes ORIGEM(.env) -> DESTINO(token informado) | {modo} ==\n")

    # ORIGEM: conta configurada (MegaSuporte). Reusa o repositório que já contorna o
    # bug de listagem da Iugu (query + registro local).
    repo = get_repo(forcar=True)
    empresas = repo.listar_ativas()
    print(f"Clientes ativos na origem: {len(empresas)}\n")

    origem = IuguClient()  # token do .env
    destino = IuguClient(api_token=args.destino_token)

    criados = pulados = erros = 0
    with origem, destino:
        for emp in empresas:
            cnpj_digits = normalizar_cnpj(emp.cnpj)
            rotulo = f"{emp.razao_social} ({emp.cnpj})"
            try:
                # Lê o customer cru da ORIGEM (identidade + endereço + notes).
                if not emp.customer_id:
                    print(f"  [PULADO] {rotulo}: sem customer_id na origem")
                    pulados += 1
                    continue
                raw = origem.get_customer(emp.customer_id)

                if _ja_existe_no_destino(destino, cnpj_digits):
                    print(f"  [JÁ EXISTE] {rotulo}")
                    pulados += 1
                    continue

                payload = {
                    "email": raw.get("email") or emp.email or "",
                    "name": raw.get("name") or emp.razao_social,
                    "cpf_cnpj": raw.get("cpf_cnpj") or emp.cnpj,
                    "notes": raw.get("notes"),  # config de negócio (JSON) — mesmo cadastro
                }
                for campo in _CAMPOS_ENDERECO:
                    if raw.get(campo):
                        payload[campo] = raw.get(campo)

                if dry:
                    print(f"  [CRIARIA] {rotulo}  email={payload['email']}")
                    criados += 1
                    continue

                novo = destino.create_customer(**payload)
                print(f"  [CRIADO] {rotulo} -> id destino {novo.get('id')}")
                criados += 1
            except IuguAPIError as e:
                print(f"  [ERRO] {rotulo}: [{e.status_code}] {e.message}")
                erros += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [ERRO] {rotulo}: {e}")
                erros += 1

    verbo = "criaria" if dry else "criados"
    print(f"\nResumo: {criados} {verbo} | {pulados} pulados (já existem/sem id) | {erros} erros")
    if dry:
        print("Foi DRY-RUN. Reveja a lista acima e rode com --executar para criar de verdade.")
    return 0 if erros == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
