"""
Servidor MCP (Model Context Protocol) para a API da Iugu.

Expõe ferramentas para gerenciar boletos/faturas via Claude (Desktop, Cowork, Claude Code).

Ferramentas expostas:
- create_boleto: cria um novo boleto
- list_boletos: lista boletos com filtros
- get_boleto: busca detalhes de um boleto específico
- cancel_boleto: cancela um boleto pendente
- search_customer: busca um cliente (útil para obter customer_id antes de criar fatura)

Para instalar no Claude Desktop, adicione ao claude_desktop_config.json:
{
  "mcpServers": {
    "iugu": {
      "command": "python",
      "args": ["-m", "mcp_iugu.server"],
      "cwd": "/caminho/absoluto/para/Integração Iugo",
      "env": {
        "IUGU_API_TOKEN": "seu_token_aqui"
      }
    }
  }
}
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# Garante que o pacote src seja importável quando rodado como script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.iugu_client import IuguAPIError, IuguClient  # noqa: E402

# Inicializa o servidor MCP
mcp = FastMCP("iugu")


def _format_invoice(inv: dict[str, Any]) -> dict[str, Any]:
    """Formata uma fatura para um resumo mais enxuto e legível."""
    return {
        "id": inv.get("id"),
        "status": inv.get("status"),
        "due_date": inv.get("due_date"),
        "paid_at": inv.get("paid_at"),
        "total": inv.get("total"),
        "total_cents": inv.get("total_cents"),
        "total_paid_cents": inv.get("total_paid_cents"),
        "customer_name": inv.get("customer_name") or (inv.get("payer") or {}).get("name"),
        "customer_email": inv.get("email") or (inv.get("payer") or {}).get("email"),
        "customer_cpf_cnpj": (inv.get("payer") or {}).get("cpf_cnpj"),
        "secure_url": inv.get("secure_url"),
        "bank_slip": {
            "digitable_line": (inv.get("bank_slip") or {}).get("digitable_line"),
            "barcode": (inv.get("bank_slip") or {}).get("barcode"),
            "bank_slip_url": (inv.get("bank_slip") or {}).get("bank_slip_url"),
        } if inv.get("bank_slip") else None,
        "items": inv.get("items"),
    }


# ============================================================
# CREATE
# ============================================================
@mcp.tool()
def create_boleto(
    email: str,
    due_date: str,
    description: str,
    price_cents: int,
    payer_name: str,
    payer_cpf_cnpj: str,
    payer_zip_code: str = "",
    payer_street: str = "",
    payer_number: str = "",
    payer_city: str = "",
    payer_state: str = "",
    payer_district: str = "",
    quantity: int = 1,
) -> dict[str, Any]:
    """
    Cria um novo boleto na Iugu.

    Args:
        email: e-mail do pagador (ex: "cliente@empresa.com")
        due_date: data de vencimento no formato YYYY-MM-DD (ex: "2026-05-15")
        description: descrição do serviço cobrado
        price_cents: valor em centavos (ex: 15000 = R$ 150,00)
        payer_name: nome completo / razão social
        payer_cpf_cnpj: CPF ou CNPJ do pagador (com ou sem formatação)
        payer_zip_code: CEP (opcional mas recomendado para boleto)
        payer_street: logradouro
        payer_number: número
        payer_city: cidade
        payer_state: UF (ex: "DF")
        payer_district: bairro
        quantity: quantidade do item (padrão 1)

    Returns:
        Dados do boleto criado incluindo linha digitável e URL pública.
    """
    payer = {
        "cpf_cnpj": payer_cpf_cnpj,
        "name": payer_name,
        "email": email,
    }
    address_fields = {
        "zip_code": payer_zip_code,
        "street": payer_street,
        "number": payer_number,
        "city": payer_city,
        "state": payer_state,
        "district": payer_district,
    }
    address = {k: v for k, v in address_fields.items() if v}
    if address:
        payer["address"] = address

    items = [{
        "description": description,
        "quantity": quantity,
        "price_cents": price_cents,
    }]

    try:
        with IuguClient() as client:
            invoice = client.create_invoice(
                email=email,
                due_date=due_date,
                items=items,
                payer=payer,
                payable_with="bank_slip",
            )
        return _format_invoice(invoice)
    except IuguAPIError as e:
        return {"error": True, "status_code": e.status_code, "message": str(e), "details": e.errors}


# ============================================================
# LIST
# ============================================================
@mcp.tool()
def list_boletos(
    status: Optional[str] = None,
    limit: int = 20,
    start: int = 0,
    due_date_from: Optional[str] = None,
    due_date_to: Optional[str] = None,
    paid_at_from: Optional[str] = None,
    paid_at_to: Optional[str] = None,
    query: Optional[str] = None,
) -> dict[str, Any]:
    """
    Lista boletos/faturas com filtros opcionais.

    Args:
        status: filtrar por status. Valores válidos:
            - "pending" (pendente)
            - "paid" (pago)
            - "canceled" (cancelado)
            - "refunded" (estornado)
            - "expired" (vencido)
            - "in_protest" (em protesto)
            - "chargeback" (chargeback)
            - "draft" (rascunho)
        limit: quantidade máxima (padrão 20, máx recomendado 100)
        start: offset para paginação
        due_date_from / due_date_to: filtro por vencimento (YYYY-MM-DD)
        paid_at_from / paid_at_to: filtro por data de pagamento
        query: busca textual livre (nome, e-mail, etc.)

    Returns:
        dict com totalItems e lista de faturas resumidas.
    """
    try:
        with IuguClient() as client:
            result = client.list_invoices(
                status=status,
                limit=limit,
                start=start,
                due_date_from=due_date_from,
                due_date_to=due_date_to,
                paid_at_from=paid_at_from,
                paid_at_to=paid_at_to,
                query=query,
            )
        items = result.get("items", [])
        return {
            "totalItems": result.get("totalItems"),
            "count": len(items),
            "items": [_format_invoice(inv) for inv in items],
        }
    except IuguAPIError as e:
        return {"error": True, "status_code": e.status_code, "message": str(e)}


# ============================================================
# GET
# ============================================================
@mcp.tool()
def get_boleto(invoice_id: str) -> dict[str, Any]:
    """
    Busca os detalhes completos de um boleto/fatura específico.

    Args:
        invoice_id: ID da fatura na Iugu (ex: "D5D5B9B5-...")

    Returns:
        Dados detalhados da fatura.
    """
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
        return _format_invoice(invoice)
    except IuguAPIError as e:
        return {"error": True, "status_code": e.status_code, "message": str(e)}


# ============================================================
# CANCEL
# ============================================================
@mcp.tool()
def cancel_boleto(invoice_id: str) -> dict[str, Any]:
    """
    Cancela um boleto pendente. Só funciona se a fatura ainda não foi paga.
    Para estornar uma fatura paga, use a função refund_boleto.

    Args:
        invoice_id: ID da fatura na Iugu

    Returns:
        Confirmação do cancelamento.
    """
    try:
        with IuguClient() as client:
            invoice = client.cancel_invoice(invoice_id)
        return {
            "success": True,
            "message": "Boleto cancelado com sucesso",
            "invoice": _format_invoice(invoice),
        }
    except IuguAPIError as e:
        return {"error": True, "status_code": e.status_code, "message": str(e)}


# ============================================================
# REFUND
# ============================================================
@mcp.tool()
def refund_boleto(
    invoice_id: str, partial_value_refund_cents: Optional[int] = None
) -> dict[str, Any]:
    """
    Estorna um boleto já pago (total ou parcial).

    Args:
        invoice_id: ID da fatura na Iugu
        partial_value_refund_cents: valor parcial a estornar em centavos.
                                    Se None, estorna o valor total.

    Returns:
        Confirmação do estorno.
    """
    try:
        with IuguClient() as client:
            invoice = client.refund_invoice(
                invoice_id, partial_value_refund_cents=partial_value_refund_cents
            )
        return {
            "success": True,
            "message": "Boleto estornado com sucesso",
            "invoice": _format_invoice(invoice),
        }
    except IuguAPIError as e:
        return {"error": True, "status_code": e.status_code, "message": str(e)}


# ============================================================
# Executar servidor MCP
# ============================================================
def main():
    """Entry point para rodar o servidor MCP via stdio (padrão para Claude Desktop)."""
    mcp.run()


if __name__ == "__main__":
    main()
