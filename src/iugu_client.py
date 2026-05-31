"""
Cliente da API Iugu.

Implementa as operações principais de faturas/boletos:
- create: criar uma nova fatura/boleto
- list: listar faturas com filtros
- get: buscar detalhes de uma fatura específica
- cancel: cancelar uma fatura
- refund: estornar uma fatura paga

A autenticação é HTTP Basic Auth onde o usuário é o API Token
e a senha é vazia.

Documentação oficial: https://dev.iugu.com/reference
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import httpx
from loguru import logger

from .config import settings


class IuguAPIError(Exception):
    """Erro retornado pela API da Iugu."""

    def __init__(self, status_code: int, message: str, errors: Any = None):
        self.status_code = status_code
        self.message = message
        self.errors = errors
        super().__init__(f"[{status_code}] {message}")


class IuguClient:
    """Cliente síncrono para a API REST da Iugu."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_token = api_token or settings.iugu_api_token
        self.base_url = (base_url or settings.iugu_api_base_url).rstrip("/")

        if not self.api_token:
            raise ValueError(
                "IUGU_API_TOKEN não configurado. Defina no .env ou passe como parâmetro."
            )

        # Iugu usa HTTP Basic Auth: token como usuário, senha vazia
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=(self.api_token, ""),
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    # -----------------------------
    # Helpers
    # -----------------------------
    def _request(self, method: str, path: str, _retries: int = 3, **kwargs) -> dict[str, Any]:
        """Executa uma requisição e trata erros de forma padronizada.

        Faz retry automático com backoff em caso de 429 (rate limit).
        """
        import time as _time

        for attempt in range(_retries):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                logger.error(f"Erro de rede ao chamar Iugu: {exc}")
                raise IuguAPIError(0, f"Falha de conexão com a Iugu: {exc}") from exc

            # Retry automático em caso de rate limit (429)
            if response.status_code == 429 and attempt < _retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"Rate limit Iugu (429) — aguardando {wait}s antes de tentar novamente (tentativa {attempt + 1}/{_retries})")
                _time.sleep(wait)
                continue

            if response.status_code >= 400:
                try:
                    body = response.json()
                except ValueError:
                    body = {"raw": response.text}
                logger.error(
                    f"Iugu retornou {response.status_code}: {body}"
                )
                raise IuguAPIError(
                    response.status_code,
                    body.get("message") or body.get("error") or response.reason_phrase,
                    body.get("errors"),
                )

            return response.json() if response.content else {}

        # Não deveria chegar aqui, mas por segurança:
        raise IuguAPIError(429, "Rate limit Iugu excedido após múltiplas tentativas")

    # -----------------------------
    # Faturas (Invoices / Boletos)
    # -----------------------------
    def create_invoice(
        self,
        email: str,
        due_date: date | str,
        items: list[dict[str, Any]],
        *,
        payer: Optional[dict[str, Any]] = None,
        payable_with: str | list[str] = "bank_slip",
        notification_url: Optional[str] = None,
        return_url: Optional[str] = None,
        custom_variables: Optional[list[dict[str, str]]] = None,
        # --- Expiração ---
        expires_in: Optional[int] = None,
        bank_slip_extra_due: Optional[int] = None,
        # --- Multa e juros ---
        fines: Optional[bool] = None,
        late_payment_fine: Optional[int] = None,
        per_day_interest: Optional[bool] = None,
        per_day_interest_value: Optional[int] = None,
        per_day_interest_cents: Optional[int] = None,
        # --- Notificações ---
        ignore_due_email: Optional[bool] = None,
        ignore_canceled_email: Optional[bool] = None,
        **extra,
    ) -> dict[str, Any]:
        """
        Cria uma nova fatura (boleto + pix por padrão).

        Args:
            email: e-mail do pagador
            due_date: data de vencimento (objeto date ou string YYYY-MM-DD)
            items: lista de itens cobrados. Cada item: {description, quantity, price_cents}
                Ex: [{"description": "Serviço X", "quantity": 1, "price_cents": 15000}]
            payer: dados do pagador (obrigatório para boleto e pix)
                Ex: {
                    "cpf_cnpj": "00.000.000/0001-00",
                    "name": "Empresa XYZ",
                    "email": "contato@empresa.com",
                    "address": {"zip_code": "70000-000", "street": "...",
                                "number": "...", "city": "Brasília",
                                "state": "DF", "district": "..."}
                }
            payable_with: método(s) de pagamento: "bank_slip" (boleto), "credit_card",
                          "pix", "all", ou lista. Padrão: apenas boleto.
            notification_url: URL para webhook específico desta fatura
            return_url: URL de retorno após pagamento
            custom_variables: metadados personalizados (lista de {name, value})
            expires_in: dias após vencimento para expirar (0-120)
            bank_slip_extra_due: dias extras para pagar boleto após vencimento (1-120)
            fines: habilitar multa por atraso
            late_payment_fine: percentual da multa (ex: 2 = 2%)
            per_day_interest: cobrar juros por dia (1% ao mês pro rata)
            per_day_interest_value: percentual customizado de juros diários
            per_day_interest_cents: juros diários em centavos (sobrepõe per_day_interest_value)
            ignore_due_email: se True, não envia e-mail de cobrança no vencimento
            ignore_canceled_email: se True, não envia e-mail de cancelamento
            **extra: outros campos aceitos pela API

        Returns:
            dict com os dados da fatura criada, incluindo:
            - id: ID único da fatura
            - secure_url: URL pública para o pagador
            - bank_slip.digitable_line: linha digitável do boleto
            - bank_slip.barcode: código de barras
            - pix.qrcode_text: código Pix copia-e-cola
            - status: "pending"
        """
        if isinstance(due_date, date):
            due_date = due_date.isoformat()

        payload: dict[str, Any] = {
            "email": email,
            "due_date": due_date,
            "items": items,
            "payable_with": payable_with,
        }
        if payer:
            payload["payer"] = payer
        if notification_url:
            payload["notification_url"] = notification_url
        if return_url:
            payload["return_url"] = return_url
        if custom_variables:
            payload["custom_variables"] = custom_variables

        # Expiração
        if expires_in is not None:
            payload["expires_in"] = str(expires_in)
        if bank_slip_extra_due is not None:
            payload["bank_slip_extra_due"] = str(bank_slip_extra_due)

        # Multa e juros
        if fines is not None:
            payload["fines"] = fines
        if late_payment_fine is not None:
            payload["late_payment_fine"] = late_payment_fine
        if per_day_interest is not None:
            payload["per_day_interest"] = per_day_interest
        if per_day_interest_value is not None:
            payload["per_day_interest_value"] = per_day_interest_value
        if per_day_interest_cents is not None:
            payload["per_day_interest_cents"] = per_day_interest_cents

        # Notificações
        if ignore_due_email is not None:
            payload["ignore_due_email"] = ignore_due_email
        if ignore_canceled_email is not None:
            payload["ignore_canceled_email"] = ignore_canceled_email

        payload.update(extra)

        logger.info(f"Criando fatura Iugu para {email} — vencimento {due_date}")
        return self._request("POST", "/v1/invoices", json=payload)

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        """Busca detalhes de uma fatura específica por ID."""
        logger.debug(f"Buscando fatura {invoice_id}")
        return self._request("GET", f"/v1/invoices/{invoice_id}")

    def list_invoices(
        self,
        *,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        created_at_from: Optional[str] = None,
        created_at_to: Optional[str] = None,
        paid_at_from: Optional[str] = None,
        paid_at_to: Optional[str] = None,
        due_date_from: Optional[str] = None,
        due_date_to: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
        sortBy: Optional[str] = None,
        query: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Lista faturas com filtros.

        Args:
            status: "pending", "paid", "canceled", "draft", "partially_paid",
                   "refunded", "expired", "in_protest", "chargeback", "in_analysis"
            customer_id: filtrar por cliente específico
            created_at_from / created_at_to: intervalo de criação (ISO 8601)
            paid_at_from / paid_at_to: intervalo de pagamento
            due_date_from / due_date_to: intervalo de vencimento
            limit: máximo de registros (default 100)
            start: offset para paginação
            query: busca textual livre

        Returns:
            dict com {totalItems, items: [...]}
        """
        # A Iugu trata os filtros '*_to' como exclusivos no inicio do dia. Se vier
        # so a data (YYYY-MM-DD), avancamos 1 dia para incluir o dia inteiro —
        # senao faturas criadas/pagas no proprio dia do limite ficam de fora
        # (ex.: faturas criadas no ultimo dia do mes nao apareciam na listagem).
        import re as _re
        from datetime import date as _date, timedelta as _timedelta

        def _to_inclusivo(v: Any) -> Any:
            if isinstance(v, str) and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                return (_date.fromisoformat(v) + _timedelta(days=1)).isoformat()
            return v

        params: dict[str, Any] = {"limit": limit, "start": start}
        if status:
            params["status_filter"] = status
        if customer_id:
            params["customer_id"] = customer_id
        if created_at_from:
            params["created_at_from"] = created_at_from
        if created_at_to:
            params["created_at_to"] = _to_inclusivo(created_at_to)
        if paid_at_from:
            params["paid_at_from"] = paid_at_from
        if paid_at_to:
            params["paid_at_to"] = _to_inclusivo(paid_at_to)
        if due_date_from:
            params["due_date_from"] = due_date_from
        if due_date_to:
            params["due_date_to"] = _to_inclusivo(due_date_to)
        if sortBy:
            params["sortBy"] = sortBy
        if query:
            params["query"] = query

        return self._request("GET", "/v1/invoices", params=params)

    def cancel_invoice(self, invoice_id: str) -> dict[str, Any]:
        """Cancela uma fatura pendente (sem reembolso — só funciona se não foi paga)."""
        logger.info(f"Cancelando fatura {invoice_id}")
        return self._request("PUT", f"/v1/invoices/{invoice_id}/cancel")

    def refund_invoice(
        self, invoice_id: str, *, partial_value_refund_cents: Optional[int] = None
    ) -> dict[str, Any]:
        """Estorna uma fatura já paga (total ou parcial)."""
        payload = {}
        if partial_value_refund_cents is not None:
            payload["partial_value_refund_cents"] = partial_value_refund_cents
        logger.info(f"Estornando fatura {invoice_id}")
        return self._request("POST", f"/v1/invoices/{invoice_id}/refund", json=payload)

    # -----------------------------
    # Clientes
    # -----------------------------
    def create_customer(
        self,
        email: str,
        name: str,
        *,
        cpf_cnpj: Optional[str] = None,
        phone: Optional[str] = None,
        phone_prefix: Optional[str] = None,
        notes: Optional[str] = None,
        cc_emails: Optional[str] = None,
        zip_code: Optional[str] = None,
        number: Optional[str] = None,
        street: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        district: Optional[str] = None,
        complement: Optional[str] = None,
        custom_variables: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Cria um novo customer na Iugu (POST /v1/customers).

        Args:
            email: e-mail do cliente (obrigatório)
            name: nome ou razão social (obrigatório)
            cpf_cnpj: CPF ou CNPJ (formatado ou só dígitos)
            phone: telefone
            phone_prefix: DDD
            notes: observações internas
            cc_emails: e-mails em cópia (separados por vírgula)
            zip_code: CEP
            number: número do endereço
            street: rua/logradouro
            city: cidade
            state: UF (2 letras)
            district: bairro
            complement: complemento
            custom_variables: metadados [{name, value}, ...]

        Returns:
            dict com dados do customer criado, incluindo:
            - id: ID único do customer na Iugu
            - email, name, cpf_cnpj, etc.
        """
        payload: dict[str, Any] = {
            "email": email,
            "name": name,
        }

        if cpf_cnpj is not None:
            payload["cpf_cnpj"] = cpf_cnpj
        if phone is not None:
            payload["phone"] = phone
        if phone_prefix is not None:
            payload["phone_prefix"] = phone_prefix
        if notes is not None:
            payload["notes"] = notes
        if cc_emails is not None:
            payload["cc_emails"] = cc_emails
        if zip_code is not None:
            payload["zip_code"] = zip_code
        if number is not None:
            payload["number"] = number
        if street is not None:
            payload["street"] = street
        if city is not None:
            payload["city"] = city
        if state is not None:
            payload["state"] = state
        if district is not None:
            payload["district"] = district
        if complement is not None:
            payload["complement"] = complement
        if custom_variables is not None:
            payload["custom_variables"] = custom_variables

        logger.info(f"Criando customer Iugu: {name} ({email})")
        return self._request("POST", "/v1/customers", json=payload)

    def update_customer(self, customer_id: str, **kwargs) -> dict[str, Any]:
        """Atualiza um customer na Iugu (PUT /v1/customers/{id}).
        Aceita os mesmos campos de create_customer: email, name, cpf_cnpj, phone, etc.
        Campos não enviados não são alterados."""
        payload = {k: v for k, v in kwargs.items() if v is not None}
        logger.info(f"Atualizando customer Iugu {customer_id}")
        return self._request("PUT", f"/v1/customers/{customer_id}", json=payload)

    def delete_customer(self, customer_id: str) -> dict[str, Any]:
        """Exclui permanentemente um customer na Iugu (DELETE /v1/customers/{id})."""
        logger.info(f"Excluindo customer Iugu {customer_id}")
        return self._request("DELETE", f"/v1/customers/{customer_id}")

    def get_customer(self, customer_id: str) -> dict[str, Any]:
        """Busca dados do cliente (útil para obter CNPJ após pagamento)."""
        return self._request("GET", f"/v1/customers/{customer_id}")

    def list_customers(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
    ) -> dict[str, Any]:
        """
        Lista customers cadastrados na conta. `query` aceita busca textual
        (nome, email, CPF/CNPJ). Retorna dict com `totalItems` e `items`.
        """
        params: dict[str, Any] = {"limit": limit, "start": start}
        if query:
            params["query"] = query
        return self._request("GET", "/v1/customers", params=params)

    # -----------------------------
    # Context manager
    # -----------------------------
    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# -----------------------------
# Utilidades de alto nível
# -----------------------------
def extract_cnpj_from_invoice(invoice: dict[str, Any]) -> Optional[str]:
    """
    Extrai o CNPJ do pagador a partir de uma fatura.

    A API da Iugu retorna os dados do pagador em formato FLAT (campos no topo):
        invoice.payer_cpf_cnpj

    Mas também tentamos formatos aninhados e a lista variables como fallback,
    porque endpoints diferentes podem responder em estruturas diferentes.
    """
    # 1. Formato flat — o padrão real retornado pela API da Iugu
    for key in ("payer_cpf_cnpj", "payer_cnpj", "cpf_cnpj", "cnpj"):
        doc = invoice.get(key)
        if doc:
            return "".join(filter(str.isdigit, str(doc)))

    # 2. Formatos aninhados (fallback defensivo)
    for path in ("payer", "customer", "buyer"):
        obj = invoice.get(path)
        if isinstance(obj, dict):
            nested_doc = obj.get("cpf_cnpj") or obj.get("cnpj")
            if nested_doc:
                return "".join(filter(str.isdigit, str(nested_doc)))

    # 3. Lista 'variables' (algumas faturas Iugu trazem o CNPJ só aqui)
    variables = invoice.get("variables") or []
    if isinstance(variables, list):
        for var in variables:
            if isinstance(var, dict) and var.get("variable") in (
                "payer.cpf_cnpj",
                "payer_cpf_cnpj",
            ):
                val = var.get("value")
                if val:
                    return "".join(filter(str.isdigit, str(val)))

    return None
