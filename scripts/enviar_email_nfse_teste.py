"""
Envio de TESTE do e-mail da NFS-e — para um destinatário arbitrário.

Reaproveita EXATAMENTE o mesmo template e a mesma função de envio do sistema
(src.email_nfse.enviar_nfse_email), apenas forçando o destinatário para o
e-mail informado em --para. Útil para validar visual + SMTP + logo + anexo
sem disparar o e-mail para o cliente real.

Fontes dos dados da nota (em ordem de preferência):
  1. --log <arquivo nfse_*.json>  → usa os dados do log (numero, codigo, valor,
     data, xml_retorno_path). Bom para teste 100% fiel ao que o cliente receberia.
  2. flags --numero/--codigo/--valor/--data  → monta os dados manualmente.
     Defaults já preenchidos com a NFS-e nº 408 (sindcondominio).
  --xml <arquivo.xml> sobrescreve o caminho do XML a anexar (útil quando o
  xml_retorno_path do log aponta para um caminho de outra máquina).

Requer SMTP configurado no .env (SMTP_HOST/SMTP_USUARIO/SMTP_SENHA).
NÃO toca na Iugu nem na planilha — só lê o log/flags e manda o e-mail.

Uso (a partir da raiz do projeto):
    ./.venv/bin/python scripts/enviar_email_nfse_teste.py --para bnreis@gmail.com
    ./.venv/bin/python scripts/enviar_email_nfse_teste.py --para x@y.com --log nfse_emitidas/nfse_XXX.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# Permite "from src..." rodando o arquivo direto (scripts/ -> raiz do projeto).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.email_nfse import enviar_nfse_email  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Envia e-mail de TESTE da NFS-e.")
    parser.add_argument("--para", required=True, help="E-mail destinatário do teste")
    parser.add_argument("--log", help="Caminho de um log nfse_*.json (opcional)")
    parser.add_argument("--xml", help="Caminho do XML a anexar (sobrescreve o do log)")
    # Defaults = NFS-e nº 408 (sindcondominio), caso não use --log.
    parser.add_argument("--numero", default="408")
    parser.add_argument("--codigo", default="B3B17DA6A")
    parser.add_argument("--valor", default="3150.00")
    parser.add_argument("--data", default="2026-06-05")
    parser.add_argument("--razao", default="SINDICONDOMINIO-DF", help="Razão social (só p/ logs)")
    parser.add_argument("--cnpj", default="")
    args = parser.parse_args()

    # Monta o dict de dados da nota.
    if args.log:
        caminho_log = Path(args.log)
        if not caminho_log.is_absolute():
            caminho_log = PROJECT_ROOT / caminho_log
        if not caminho_log.exists():
            print(f"[X] Log não encontrado: {caminho_log}")
            return 1
        dados = json.loads(caminho_log.read_text(encoding="utf-8-sig"))
        print(f"[i] Dados carregados do log: {caminho_log}")
    else:
        dados = {
            "numero_nfse": args.numero,
            "codigo_verificacao": args.codigo,
            "valor": args.valor,
            "data_emissao": args.data,
            "razao_social": args.razao,
            "cnpj": args.cnpj,
        }
        print("[i] Dados montados via flags (defaults = NFS-e nº 408).")

    # --xml sobrescreve o anexo (ex.: quando o xml_retorno_path do log é de outra máquina).
    if args.xml:
        caminho_xml = Path(args.xml)
        if not caminho_xml.is_absolute():
            caminho_xml = PROJECT_ROOT / caminho_xml
        dados["xml_retorno_path"] = str(caminho_xml)
        print(f"[i] XML a anexar (sobrescrito): {caminho_xml}")

    # Empresa "tomadora" fictícia: só o e-mail importa (destinatário do teste).
    empresa = SimpleNamespace(
        razao_social=dados.get("razao_social", "Cliente"),
        cnpj=dados.get("cnpj", ""),
        email=args.para,
    )

    print(f"[i] Enviando e-mail de TESTE da NFS-e nº {dados.get('numero_nfse')} para {args.para} ...")
    ok = enviar_nfse_email(empresa, dados)
    if ok:
        print(f"[OK] E-mail enviado para {args.para}. Confira a caixa de entrada (e o spam).")
        return 0
    print("[X] Envio falhou. Veja os logs acima / journalctl -u iugu-webhook.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
