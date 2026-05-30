#!/usr/bin/env python
"""
Script de DEBUG para investigar por que o webhook não disparou.

Uso:
    python scripts/debug_webhook.py 9D6CFFE2621440C3B4D50F78A317EFA0

Este script vai:
1. Testar conexão com Iugu API
2. Buscar dados da fatura
3. Testar servidor webhook local
4. Reprocessar a fatura (simula webhook)
5. Mostrar resumo dos problemas encontrados
"""
import sys
import json
from pathlib import Path
from typing import Optional
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

# Adiciona src/ ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings
from src.iugu_client import IuguClient, extract_cnpj_from_invoice
from src.spreadsheet import EmpresasRepository

console = Console()


def test_iugu_connection():
    """Testa conexão com API da Iugu."""
    console.print("\n[bold cyan]1. Testando conexão com Iugu API[/bold cyan]")
    try:
        with IuguClient() as client:
            # Tenta buscar account info
            response = client.client.get("/accounts")
            if response.status_code == 200:
                console.print("[green]✓ Conexão com Iugu API OK[/green]")
                return True
    except Exception as e:
        console.print(f"[red]✗ Erro ao conectar com Iugu API: {e}[/red]")
        return False


def fetch_invoice_details(invoice_id: str) -> Optional[dict]:
    """Busca detalhes da fatura na Iugu."""
    console.print(f"\n[bold cyan]2. Buscando fatura: {invoice_id}[/bold cyan]")
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)

            # Exibe dados da fatura
            table = Table(title="Dados da Fatura")
            table.add_column("Campo", style="cyan")
            table.add_column("Valor")

            table.add_row("ID", invoice.get("id"))
            table.add_row("Status", invoice.get("status"))
            table.add_row("Total (R$)", f"{invoice.get('total_cents', 0) / 100:.2f}")
            table.add_row("Data criação", invoice.get("created_at", "N/A"))
            table.add_row("Data pagamento", invoice.get("paid_at", "N/A"))

            console.print(table)

            # Verifica status
            status = invoice.get("status")
            if status == "paid":
                console.print("[green]✓ Fatura está PAGA[/green]")
            else:
                console.print(f"[yellow]⚠️  Fatura com status: {status} (não paga)[/yellow]")

            return invoice
    except Exception as e:
        console.print(f"[red]✗ Erro ao buscar fatura: {e}[/red]")
        return None


def analyze_invoice(invoice: dict) -> tuple[Optional[str], str]:
    """Analisa a fatura e retorna CNPJ + motivo se houver problema."""
    console.print("\n[bold cyan]3. Analisando fatura[/bold cyan]")

    # Extrai CNPJ
    cnpj = extract_cnpj_from_invoice(invoice)
    if not cnpj:
        console.print("[red]✗ Não foi possível extrair CNPJ da fatura[/red]")
        return None, "CNPJ não encontrado"

    console.print(f"[green]✓ CNPJ extraído: {cnpj}[/green]")

    # Valida na planilha
    try:
        repo = EmpresasRepository()
        repo.carregar(forcar=True)
        empresa = repo.buscar_por_cnpj(cnpj)

        if not empresa:
            msg = f"CNPJ {cnpj} NÃO está na planilha de empresas autorizadas"
            console.print(f"[yellow]⚠️  {msg}[/yellow]")
            return cnpj, msg

        console.print(f"[green]✓ Empresa encontrada: {empresa.razao_social}[/green]")

        # Verifica flags
        if not empresa.emitir_nf:
            msg = f"Empresa {empresa.razao_social} tem emitir_nf=False"
            console.print(f"[yellow]⚠️  {msg}[/yellow]")
            return cnpj, msg

        console.print(f"[green]✓ Emissão de NFS-e está habilitada[/green]")

        return cnpj, ""
    except FileNotFoundError as e:
        msg = f"Erro ao carregar planilha: {e}"
        console.print(f"[red]✗ {msg}[/red]")
        return cnpj, msg


def test_webhook_server() -> bool:
    """Testa se o servidor webhook está rodando."""
    console.print("\n[bold cyan]4. Testando servidor webhook local[/bold cyan]")

    try:
        import httpx

        url = f"http://localhost:{settings.webhook_port}/health"
        response = httpx.get(url, timeout=5.0)

        if response.status_code == 200:
            console.print(f"[green]✓ Servidor rodando em {url}[/green]")
            console.print(f"   Resposta: {response.json()}")
            return True
    except Exception as e:
        console.print(f"[red]✗ Servidor não está acessível: {e}[/red]")
        console.print(f"   Execute: uvicorn src.webhook_server:app --reload --port {settings.webhook_port}")
        return False


def test_manual_reprocessing(invoice_id: str, invoice: dict) -> bool:
    """Testa o reprocessamento manual da fatura."""
    console.print(f"\n[bold cyan]5. Testando reprocessamento manual[/bold cyan]")

    try:
        from src.webhook_server import processar_pagamento
        import asyncio

        console.print(f"Simulando webhook para fatura {invoice_id}...")

        # Roda o processamento de pagamento
        resultado = asyncio.run(processar_pagamento(invoice_id))

        # Exibe resultado
        table = Table(title="Resultado do Processamento")
        table.add_column("Campo", style="cyan")
        table.add_column("Valor")

        for chave, valor in resultado.items():
            if chave == "nfse" and isinstance(valor, dict):
                table.add_row(chave, json.dumps(valor, indent=2, ensure_ascii=False))
            else:
                table.add_row(chave, str(valor))

        console.print(table)

        if resultado.get("success"):
            console.print("[green]✓ Reprocessamento bem-sucedido![/green]")
            return True
        else:
            console.print(f"[yellow]⚠️  Reprocessamento retornou sucesso=False[/yellow]")
            console.print(f"   Motivo: {resultado.get('error', resultado.get('motivo'))}")
            return False
    except Exception as e:
        console.print(f"[red]✗ Erro ao reprocessar: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return False


def check_webhook_registration() -> bool:
    """Dá instruções para verificar se webhook está registrado na Iugu."""
    console.print("\n[bold cyan]6. Verificação de Webhook na Iugu[/bold cyan]")

    console.print("""
[yellow]⚠️  Este script não consegue listar webhooks da Iugu (API limitada).[/yellow]

Para verificar se o webhook está registrado:
1. Acesse: https://app.iugu.com/
2. Navegue até: Configurações → Webhooks (ou similar)
3. Procure por um webhook para:
   - URL: https://seu-dominio/webhook/iugu
   - Eventos: invoice.status_changed

[bold cyan]Se o webhook NÃO está lá:[/bold cyan]
- Você precisa registrá-lo manualmente
- Use cloudflared para expor: cloudflared tunnel --url http://localhost:8000
- Registre a URL gerada no painel de webhooks

[bold cyan]Se o webhook ESTÁ lá:[/bold cyan]
- Valide o token (X-Iugu-Token header)
- Teste enviar uma fatura de teste manualmente
    """)

    return True


def main():
    """Executa todos os testes."""
    if len(sys.argv) < 2:
        console.print(f"[red]Uso: python scripts/debug_webhook.py <invoice_id>[/red]")
        console.print(f"Exemplo: python scripts/debug_webhook.py 9D6CFFE2621440C3B4D50F78A317EFA0")
        sys.exit(1)

    invoice_id = sys.argv[1]

    console.print(Panel(
        f"[bold]Debug Webhook - Fatura: {invoice_id}[/bold]",
        expand=False,
        border_style="cyan"
    ))

    # Executa testes
    tests = []

    # Teste 1: Conexão Iugu
    test1 = test_iugu_connection()
    tests.append(("Conexão Iugu API", test1))

    if not test1:
        console.print("[red]Parando: sem conexão com Iugu[/red]")
        show_summary(tests)
        sys.exit(1)

    # Teste 2: Buscar fatura
    invoice = fetch_invoice_details(invoice_id)
    test2 = invoice is not None
    tests.append(("Buscar fatura", test2))

    if not test2:
        console.print("[red]Parando: fatura não encontrada[/red]")
        show_summary(tests)
        sys.exit(1)

    # Teste 3: Analisar fatura
    cnpj, problema = analyze_invoice(invoice)
    test3 = problema == ""
    tests.append(("Análise da fatura", test3))

    # Teste 4: Servidor webhook
    test4 = test_webhook_server()
    tests.append(("Servidor webhook local", test4))

    # Teste 5: Reprocessamento manual
    test5 = False
    if test4:  # Só tenta se servidor está rodando
        test5 = test_manual_reprocessing(invoice_id, invoice)
        tests.append(("Reprocessamento manual", test5))
    else:
        tests.append(("Reprocessamento manual", "PULADO"))

    # Teste 6: Instruções
    test_webhook_registration()
    tests.append(("Registrado na Iugu", "?"))

    # Mostra resumo
    show_summary(tests)

    # Se tudo passou, sucesso
    if all(isinstance(t[1], bool) and t[1] for t in tests[:-1]):
        console.print("\n[bold green]🎉 TODOS OS TESTES PASSARAM![/bold green]")
        console.print("Webhook está funcionando corretamente.")
    else:
        console.print("\n[bold yellow]⚠️  Alguns problemas foram encontrados[/bold yellow]")


def show_summary(tests):
    """Mostra resumo dos testes."""
    console.print("\n" + "="*60)
    console.print("[bold cyan]RESUMO DOS TESTES[/bold cyan]")
    console.print("="*60)

    table = Table()
    table.add_column("Teste", style="cyan")
    table.add_column("Status", style="bold")

    for nome, resultado in tests:
        if resultado is True:
            status = "[green]✓ PASSOU[/green]"
        elif resultado is False:
            status = "[red]✗ FALHOU[/red]"
        else:
            status = f"[yellow]{resultado}[/yellow]"
        table.add_row(nome, status)

    console.print(table)
    console.print("="*60)


if __name__ == "__main__":
    main()
