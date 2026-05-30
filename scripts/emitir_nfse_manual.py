"""
Emite uma NFS-e manualmente para testes/homologação.

Fluxo:
1. Recebe um invoice_id da Iugu via CLI
2. Busca a fatura na Iugu (precisa estar paga, ou --forcar)
3. Verifica o CNPJ na planilha
4. Monta a DPS, assina com certificado A1 e envia ao webservice do DF
5. Arquiva XML enviado + retorno + imprime resultado

Uso:
    # Emite NFS-e real (vai até o webservice)
    python scripts/emitir_nfse_manual.py <invoice_id>

    # Apenas monta e assina a DPS (não envia). Útil para homologação inicial
    # onde você quer validar o XML antes de ter a URL do webservice.
    python scripts/emitir_nfse_manual.py <invoice_id> --dry-run

    # Monta DPS com dados fictícios (não precisa de invoice_id real)
    python scripts/emitir_nfse_manual.py --exemplo

    # Testa em PRODUÇÃO com R$1,00 (gera XML + assina, sem enviar)
    python scripts/emitir_nfse_manual.py --exemplo --producao --valor 1.00 --dry-run

    # Testa em PRODUÇÃO com R$1,00 (envia de verdade — gera NFS-e real!)
    python scripts/emitir_nfse_manual.py --exemplo --producao --valor 1.00

    # Força emissão mesmo se fatura não estiver paga (homologação)
    python scripts/emitir_nfse_manual.py <invoice_id> --forcar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger

from src.config import settings
from src.iugu_client import IuguAPIError, IuguClient, extract_cnpj_from_invoice
from src.nfse_df import emitir_nfse
from src.spreadsheet import Empresa, EmpresasRepository


def _fatura_exemplo() -> dict:
    """Fatura fictícia para testes — simula uma fatura Iugu paga."""
    return {
        "id": "EXEMPLO123456789",
        "status": "paid",
        "total_cents": 185000,
        "total_paid_cents": 185000,
        "payer_name": "Empresa Tomadora LTDA",
        "payer_cpf_cnpj": "50214976000135",
        "payer_email": "financeiro@tomadora.com.br",
        "payer_address_street": "SCS Quadra 1",
        "payer_address_number": "100",
        "payer_address_complement": "Sala 500",
        "payer_address_district": "Asa Sul",
        "payer_address_city": "Brasília",
        "payer_address_state": "DF",
        "payer_address_zip_code": "70300-000",
        "items": [
            {
                "description": "Mensalidade de suporte técnico — Abril/2026",
                "quantity": 1,
                "price_cents": 185000,
            }
        ],
    }


def _empresa_exemplo() -> Empresa:
    """Empresa fictícia para testes."""
    return Empresa(
        cnpj="50214976000135",
        razao_social="Empresa Tomadora LTDA",
        email="financeiro@tomadora.com.br",
        codigo_servico="01.07",
        descricao_servico="Suporte técnico em informática",
        aliquota_iss=2.0,
        emitir_nf=True,
        descricao_boleto="Mensalidade — Abril/2026",
        valor_fatura="1850,00",
        dia_criacao_fatura=10,
        ativo=True,
    )


async def _executar(invoice: dict, empresa: Empresa, dry_run: bool):
    """Executa o fluxo de emissão."""
    if dry_run:
        # Importa lazy para evitar dependências pesadas no caminho de validação
        from src.nfse_df import (
            _assinar_xml,
            _montar_xml_dps,
            _proximo_numero_dps,
            _validar_configuracao,
            DadosServico,
            extrair_endereco_tomador,
        )

        problemas = _validar_configuracao(empresa)
        if problemas:
            print("\n❌ Configuração incompleta para DPS:")
            for p in problemas:
                print(f"   • {p}")
            return

        total_cents = int(
            invoice.get("total_paid_cents") or invoice.get("total_cents") or 0
        )
        servico = DadosServico(
            codigo_servico=empresa.codigo_servico or settings.nfse_codigo_servico_padrao,
            descricao=empresa.descricao_servico or "Serviço",
            valor_cents=total_cents,
            aliquota_iss=empresa.aliquota_iss or settings.nfse_aliquota_iss_padrao,
        )

        numero = _proximo_numero_dps()
        xml, dps_id = _montar_xml_dps(
            empresa=empresa,
            servico=servico,
            endereco_tomador=extrair_endereco_tomador(invoice),
            invoice=invoice,
            numero_dps=numero,
        )

        print(f"\n✅ DPS montada (número {numero}, id {dps_id})")
        print(f"Primeiros 500 chars do XML:\n")
        print(xml[:500] + "...")

        try:
            xml_assinado = _assinar_xml(xml, dps_id)
            print(f"\n✅ DPS assinada com sucesso ({len(xml_assinado)} bytes)")
        except Exception as exc:
            print(f"\n⚠️  Não foi possível assinar: {exc}")
            print("   (certificado A1 ausente ou senha incorreta)")

        out = Path(settings.nfse_output_dir) / f"dps_exemplo_{numero}.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(xml, encoding="utf-8")
        print(f"\n📄 XML não-assinado salvo em: {out}")
        return

    # Modo real: chama emitir_nfse completo
    resultado = await emitir_nfse(invoice=invoice, empresa=empresa)
    print("\n" + json.dumps(resultado, indent=2, ensure_ascii=False))
    if resultado["sucesso"]:
        print(f"\n🎉 NFS-e emitida! Número: {resultado['numero_nfse']}")
    else:
        print(f"\n❌ Falha: {resultado['mensagem_erro']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("invoice_id", nargs="?", help="ID da fatura na Iugu (omita se --exemplo)")
    parser.add_argument(
        "--exemplo",
        action="store_true",
        help="Usa fatura e empresa fictícias (não consulta Iugu nem planilha)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas monta e assina a DPS, não envia ao webservice",
    )
    parser.add_argument(
        "--forcar",
        action="store_true",
        help="Emite mesmo se a fatura não estiver paga (para testes)",
    )
    parser.add_argument(
        "--producao",
        action="store_true",
        help="Força ambiente de PRODUÇÃO (tpAmb=1) independente do .env",
    )
    parser.add_argument(
        "--valor",
        type=float,
        default=None,
        help="Valor em reais (ex: 1.00) — sobrescreve o valor do exemplo",
    )
    args = parser.parse_args()

    if args.exemplo:
        invoice = _fatura_exemplo()
        empresa = _empresa_exemplo()
        logger.info("Usando fatura e empresa fictícias")
    else:
        if not args.invoice_id:
            parser.error("invoice_id é obrigatório (ou use --exemplo)")

        # Busca fatura real
        try:
            with IuguClient() as client:
                invoice = client.get_invoice(args.invoice_id)
        except IuguAPIError as exc:
            print(f"❌ Erro ao buscar fatura na Iugu: {exc}")
            sys.exit(1)

        if invoice.get("status") != "paid" and not args.forcar:
            print(
                f"❌ Fatura não está paga (status={invoice.get('status')}). "
                f"Use --forcar para emitir mesmo assim."
            )
            sys.exit(1)

        # Busca empresa na planilha
        cnpj = extract_cnpj_from_invoice(invoice)
        if not cnpj:
            print("❌ CNPJ não encontrado na fatura")
            sys.exit(1)

        repo = EmpresasRepository()
        repo.carregar()
        empresa = repo.buscar_por_cnpj(cnpj)
        if not empresa:
            print(f"❌ CNPJ {cnpj} não está na planilha")
            sys.exit(1)

    # Override de ambiente para produção
    if args.producao:
        settings.nfse_ambiente = "producao"
        logger.warning("⚠️  AMBIENTE FORÇADO PARA PRODUÇÃO (tpAmb=1)")

    # Override de valor
    if args.valor is not None:
        valor_cents = int(round(args.valor * 100))
        invoice["total_cents"] = valor_cents
        invoice["total_paid_cents"] = valor_cents
        if invoice.get("items"):
            invoice["items"][0]["price_cents"] = valor_cents
        logger.info(f"Valor sobrescrito para R$ {args.valor:.2f} ({valor_cents} centavos)")

    print(f"\n🧾 Tomador: {empresa.razao_social} ({empresa.cnpj})")
    print(f"📍 Ambiente NFS-e: {settings.nfse_ambiente}")
    print(f"💰 Valor: R$ {int(invoice.get('total_paid_cents') or invoice.get('total_cents', 0)) / 100:.2f}")
    print(f"🔒 Certificado: {settings.nfse_certificado_path}")

    if args.producao and not args.dry_run:
        resp = input("\n⚠️  ATENÇÃO: Vai enviar para PRODUÇÃO! Gerar NFS-e real? (s/N): ")
        if resp.strip().lower() != "s":
            print("Cancelado.")
            return

    asyncio.run(_executar(invoice, empresa, args.dry_run))


if __name__ == "__main__":
    main()
