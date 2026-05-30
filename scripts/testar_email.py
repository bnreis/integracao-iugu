"""
Script de teste para validar o envio de e-mail de NFS-e.

Envia um e-mail de teste com um XML fictício para confirmar
que as configurações SMTP estão corretas.

Uso:
    python scripts/testar_email.py
    python scripts/testar_email.py --para outro@email.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ajusta o path para importar os módulos do projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.email_nfse import enviar_nfse_email, _smtp_configurado
from src.spreadsheet import Empresa


def main():
    parser = argparse.ArgumentParser(description="Testa o envio de e-mail de NFS-e")
    parser.add_argument(
        "--para",
        default="bnreis@gmail.com",
        help="E-mail destinatário do teste (default: bnreis@gmail.com)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  TESTE DE ENVIO DE E-MAIL — NFS-e")
    print("=" * 60)
    print()

    # 1. Verifica configuração SMTP
    print(f"  SMTP_HOST:      {settings.smtp_host or '(vazio)'}")
    print(f"  SMTP_PORT:      {settings.smtp_port}")
    print(f"  SMTP_USUARIO:   {settings.smtp_usuario or '(vazio)'}")
    print(f"  SMTP_SENHA:     {'*' * len(settings.smtp_senha) if settings.smtp_senha else '(vazio)'}")
    print(f"  SMTP_USAR_TLS:  {settings.smtp_usar_tls}")
    print(f"  Remetente:      {settings.smtp_remetente_nome} <{settings.smtp_remetente_email or settings.smtp_usuario}>")
    print()

    if not _smtp_configurado():
        print("❌ SMTP não configurado! Preencha SMTP_HOST, SMTP_USUARIO e SMTP_SENHA no .env")
        sys.exit(1)

    print("✅ Configuração SMTP detectada")
    print()

    # 2. Cria dados fictícios para o teste
    empresa_teste = Empresa(
        cnpj="36342291000143",
        razao_social="EMPRESA TESTE LTDA",
        email=args.para,
        emitir_nf=True,
    )

    nfse_ficticia = {
        "sucesso": True,
        "numero_nfse": "TESTE-001",
        "codigo_verificacao": "ABC123DEF456",
        "xml_enviado_path": None,  # Sem anexo no teste
        "xml_retorno_path": None,
        "pdf_path": None,
        "mensagens": ["Este é um e-mail de teste"],
        "ambiente": "teste",
    }

    print(f"  Destinatário:   {args.para}")
    print(f"  NFS-e Nº:       {nfse_ficticia['numero_nfse']}")
    print(f"  Prestador:      {settings.nfse_razao_social_prestador}")
    print()
    print("Enviando e-mail de teste...")
    print()

    # 3. Envia
    sucesso = enviar_nfse_email(empresa_teste, nfse_ficticia)

    if sucesso:
        print()
        print("=" * 60)
        print(f"  ✅ E-mail enviado com sucesso para {args.para}")
        print("  Verifique a caixa de entrada (e o spam).")
        print("=" * 60)
    else:
        print()
        print("=" * 60)
        print("  ❌ Falha no envio. Verifique os logs acima.")
        print("  Dicas:")
        print("  - Confirme que a Senha de App está correta")
        print("  - Verifique se a Verificação em 2 etapas está ativa")
        print("  - Teste acessando: https://myaccount.google.com/apppasswords")
        print("=" * 60)

    sys.exit(0 if sucesso else 1)


if __name__ == "__main__":
    main()
