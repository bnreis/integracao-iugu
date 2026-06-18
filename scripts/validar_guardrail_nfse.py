"""Validador OFFLINE do guardrail anti-duplicata de NFS-e (src/nfse_guard.py).

Exercita _verificar_nfse_duplicada contra TODOS os cenários do sistema, usando um
diretório temporário de logs (não toca em produção). Roda sem rede.

    python scripts/validar_guardrail_nfse.py

Cenários cobertos:
  1. Reprocessar a MESMA fatura (retry webhook/cron)            -> BLOQUEIA (regra 1)
  2. 2ª fatura, mesmo cliente, mesmo mês, MESMO valor           -> BLOQUEIA (cliente_mes)
  3. 2ª fatura, mesmo cliente, mesmo mês, valor DIFERENTE       -> BLOQUEIA (novo)
  4. ISS retido: cancelada+recriada no mês (bruto x líquido)    -> BLOQUEIA
  5. Cliente DIFERENTE, mesmo mês                                -> permite
  6. Mesmo cliente, mês ANTERIOR (recorrência mensal normal)    -> permite
  7. Log de REJEIÇÃO (sucesso=False) da própria fatura          -> permite (retry)
  8. CNPJ com máscara x sem máscara (robustez de formato)       -> BLOQUEIA
  9. Marcação MANUAL "NF-e já emitida" trava o cliente no mês   -> BLOQUEIA
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

# Permite importar o pacote src/ rodando da raiz do projeto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.nfse_guard import _verificar_nfse_duplicada  # noqa: E402

MES_ATUAL = date.today().isoformat()[:7]  # "2026-06"
# Um mês claramente anterior (não depende do mês corrente): usa o ano-1, mês 01.
ano_ant = int(MES_ATUAL[:4]) - 1
MES_ANTERIOR = f"{ano_ant}-01"

CNPJ_A = "36342291000143"  # só dígitos (como o sistema normaliza)
CNPJ_B = "11222333000181"


def _escrever_log(
    d: Path,
    invoice_id: str,
    cnpj: str,
    valor: float,
    data_emissao: str,
    sucesso: bool = True,
    marcada_manualmente: bool = False,
    numero_nfse: str | None = "100",
) -> None:
    log = {
        "invoice_id": invoice_id,
        "numero_nfse": numero_nfse,
        "cnpj": cnpj,
        "valor": valor,
        "data_emissao": data_emissao,
        "sucesso": sucesso,
        "marcada_manualmente": marcada_manualmente,
    }
    (d / f"nfse_{invoice_id}.json").write_text(
        json.dumps(log, ensure_ascii=False), encoding="utf-8"
    )


def _fatura(invoice_id: str, total_cents: int, paid_mes: str) -> dict:
    """Monta uma fatura mínima como a Iugu entrega (paid_at no 1º dia do mês)."""
    return {
        "id": invoice_id,
        "total_cents": total_cents,
        "total_paid_cents": total_cents,
        "paid_at": f"{paid_mes}-01T10:00:00-03:00",
    }


def _cenario(nome: str, esperado_bloqueio: bool, resultado: dict | None) -> bool:
    bloqueou = resultado is not None
    ok = bloqueou == esperado_bloqueio
    marca = "OK " if ok else "FALHA"
    esperado = "BLOQUEIA" if esperado_bloqueio else "permite"
    obtido = (
        f"BLOQUEOU ({resultado.get('fonte')})" if bloqueou else "permitiu"
    )
    print(f"  [{marca}] {nome}")
    print(f"         esperado: {esperado:9} | obtido: {obtido}")
    if bloqueou:
        print(f"         detalhe : {resultado.get('detalhe')}")
    return ok


def main() -> int:
    print(f"Mês atual (referência) = {MES_ATUAL} | mês anterior usado = {MES_ANTERIOR}\n")
    falhas = 0

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        settings.nfse_output_dir = str(d)  # redireciona o guardrail p/ o temp

        # --- Cenário 1: mesma fatura reprocessada (regra 1) ---
        _escrever_log(d, "INV_A", CNPJ_A, 100.0, f"{MES_ATUAL}-10")
        r = _verificar_nfse_duplicada("INV_A", CNPJ_A, _fatura("INV_A", 10000, MES_ATUAL))
        falhas += not _cenario("1. Reprocessar a MESMA fatura", True, r)

        # --- Cenário 2: 2ª fatura, mesmo cliente/mês, MESMO valor ---
        r = _verificar_nfse_duplicada("INV_B", CNPJ_A, _fatura("INV_B", 10000, MES_ATUAL))
        falhas += not _cenario("2. 2a fatura, mesmo cliente/mes, MESMO valor", True, r)

        # --- Cenário 3: 2ª fatura, mesmo cliente/mês, valor DIFERENTE (novo) ---
        r = _verificar_nfse_duplicada("INV_C", CNPJ_A, _fatura("INV_C", 25000, MES_ATUAL))
        falhas += not _cenario("3. 2a fatura, mesmo cliente/mes, valor DIFERENTE", True, r)

        # --- Cenário 4: ISS retido cancelada+recriada (log=bruto 3276, fatura=líquido 3210,48) ---
        d2 = Path(tempfile.mkdtemp())
        settings.nfse_output_dir = str(d2)
        _escrever_log(d2, "INV_OLD", CNPJ_A, 3276.00, f"{MES_ATUAL}-05")
        r = _verificar_nfse_duplicada("INV_NEW", CNPJ_A, _fatura("INV_NEW", 321048, MES_ATUAL))
        falhas += not _cenario("4. ISS retido cancelada+recriada (bruto x liquido)", True, r)

        # --- Cenário 5: cliente DIFERENTE, mesmo mês ---
        r = _verificar_nfse_duplicada("INV_OTH", CNPJ_B, _fatura("INV_OTH", 10000, MES_ATUAL))
        falhas += not _cenario("5. Cliente DIFERENTE, mesmo mes", False, r)

        # --- Cenário 6: mesmo cliente, mês ANTERIOR (recorrência normal) ---
        d3 = Path(tempfile.mkdtemp())
        settings.nfse_output_dir = str(d3)
        _escrever_log(d3, "INV_PREV", CNPJ_A, 100.0, f"{MES_ANTERIOR}-15")
        r = _verificar_nfse_duplicada("INV_CUR", CNPJ_A, _fatura("INV_CUR", 10000, MES_ATUAL))
        falhas += not _cenario("6. Mesmo cliente, mes ANTERIOR (recorrencia)", False, r)

        # --- Cenário 7: log de REJEIÇÃO (sucesso=False) da própria fatura ---
        d4 = Path(tempfile.mkdtemp())
        settings.nfse_output_dir = str(d4)
        _escrever_log(d4, "INV_REJ", CNPJ_A, 100.0, f"{MES_ATUAL}-10", sucesso=False)
        r = _verificar_nfse_duplicada("INV_REJ", CNPJ_A, _fatura("INV_REJ", 10000, MES_ATUAL))
        falhas += not _cenario("7. Log de REJEICAO (sucesso=False) -> permite retry", False, r)

        # --- Cenário 8: CNPJ com máscara x sem máscara (robustez de formato) ---
        d5 = Path(tempfile.mkdtemp())
        settings.nfse_output_dir = str(d5)
        _escrever_log(d5, "INV_FMT", "36.342.291/0001-43", 100.0, f"{MES_ATUAL}-10")
        r = _verificar_nfse_duplicada("INV_FMT2", CNPJ_A, _fatura("INV_FMT2", 10000, MES_ATUAL))
        falhas += not _cenario("8. CNPJ com mascara x sem mascara", True, r)

        # --- Cenário 9: marcação MANUAL trava o cliente no mês ---
        d6 = Path(tempfile.mkdtemp())
        settings.nfse_output_dir = str(d6)
        _escrever_log(
            d6, "INV_MARK", CNPJ_A, 0.0, f"{MES_ATUAL}-12",
            marcada_manualmente=True, numero_nfse=None,
        )
        r = _verificar_nfse_duplicada("INV_NEW2", CNPJ_A, _fatura("INV_NEW2", 50000, MES_ATUAL))
        falhas += not _cenario("9. Marcacao MANUAL trava o cliente no mes", True, r)

    print()
    if falhas:
        print(f"RESULTADO: {falhas} cenario(s) FALHARAM.")
        return 1
    print("RESULTADO: todos os 9 cenarios passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
