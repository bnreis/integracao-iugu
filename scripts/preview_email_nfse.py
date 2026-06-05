"""
Preview do e-mail da NFS-e — SEM ENVIAR.

Monta o e-mail da NFS-e nº 408 (lendo o log
nfse_emitidas/nfse_6CCA24035D344354A311792186B9962F.json) e salva apenas o
HTML do corpo em nfse_emitidas/preview_email_nfse.html, para o Bruno abrir no
navegador e revisar o visual.

NÃO faz envio SMTP nem qualquer chamada de rede.

Uso (a partir da raiz do projeto):
    PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/preview_email_nfse.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# Permite "from src..." rodando o arquivo direto (scripts/ -> raiz do projeto).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.email_nfse import montar_email_nfse  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_408 = PROJECT_ROOT / "nfse_emitidas" / "nfse_6CCA24035D344354A311792186B9962F.json"
SAIDA_HTML = PROJECT_ROOT / "nfse_emitidas" / "preview_email_nfse.html"


def main() -> int:
    if not LOG_408.exists():
        print(f"[X] Log da NFS-e #408 não encontrado: {LOG_408}")
        return 1

    dados = json.loads(LOG_408.read_text(encoding="utf-8"))

    # Empresa tomadora simulada a partir do log (sem tocar na Iugu/planilha).
    # razao_social vem do log; e-mail é fictício só para preencher o cabeçalho.
    empresa = SimpleNamespace(
        razao_social=dados.get("razao_social", "Cliente"),
        cnpj=dados.get("cnpj", ""),
        email="cliente@exemplo.com.br",
    )

    # montar_email_nfse não envia nada — só monta a mensagem MIME e devolve o HTML.
    _msg, html, destinatarios = montar_email_nfse(empresa, dados)

    # No e-mail real a logo vai como anexo inline (cid:logo_megasuporte) — o que
    # renderiza nos clientes de e-mail, mas NÃO no navegador. Só para o PREVIEW,
    # trocamos o cid: por uma data-URI base64 da logo, pra ela aparecer ao abrir
    # o HTML. (O e-mail enviado continua usando CID, que é o correto.)
    _logo = PROJECT_ROOT / "assets" / "logo_megasuporte.png"
    if _logo.exists():
        _b64 = base64.b64encode(_logo.read_bytes()).decode("ascii")
        html = html.replace("cid:logo_megasuporte", f"data:image/png;base64,{_b64}")

    SAIDA_HTML.write_text(html, encoding="utf-8")

    print("[OK] Preview gerado (nenhum e-mail enviado).")
    print(f"     Arquivo: {SAIDA_HTML}")
    print(f"     NFS-e nº: {dados.get('numero_nfse')}")
    print(f"     Código de verificação: {dados.get('codigo_verificacao')}")
    print(f"     Valor: {dados.get('valor')}")
    print(f"     Destinatário(s) (simulado): {destinatarios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
