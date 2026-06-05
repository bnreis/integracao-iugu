"""
Módulo de envio de NFS-e por e-mail.

Envia o XML da NFS-e emitida como anexo para o e-mail do cliente (tomador).
Usado em dois fluxos:
  1. NFS-e emitida na CRIAÇÃO da fatura (scheduled_invoices.py → nf_na_criacao=True)
  2. NFS-e emitida no PAGAMENTO da fatura (webhook_server.py → fluxo padrão)

Requisitos:
  - Configurar SMTP_HOST, SMTP_USUARIO e SMTP_SENHA no .env
  - O e-mail do destinatário vem do campo `email` do objeto Empresa (planilha)

Se as configurações SMTP não estiverem preenchidas, loga um aviso e retorna
sem erro (o fluxo principal não é interrompido por falha de e-mail).
"""
from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .config import settings
from .iugu_empresas import Empresa


def _smtp_configurado() -> bool:
    """Verifica se as configurações mínimas de SMTP estão preenchidas."""
    return bool(settings.smtp_host and settings.smtp_usuario and settings.smtp_senha)


def _remetente() -> str:
    """Retorna o e-mail do remetente (campo dedicado ou o usuário SMTP)."""
    return settings.smtp_remetente_email or settings.smtp_usuario


def _anexar_arquivo(msg: MIMEMultipart, caminho: Path, nome_arquivo: Optional[str] = None) -> bool:
    """Anexa um arquivo ao e-mail. Retorna True se conseguiu."""
    if not caminho or not caminho.exists():
        logger.debug(f"Arquivo não encontrado para anexar: {caminho}")
        return False
    nome = nome_arquivo or caminho.name
    try:
        with open(caminho, "rb") as f:
            parte = MIMEApplication(f.read(), Name=nome)
        parte["Content-Disposition"] = f'attachment; filename="{nome}"'
        msg.attach(parte)
        return True
    except Exception as exc:
        logger.warning(f"Falha ao anexar {caminho}: {exc}")
        return False


def enviar_nfse_email(
    empresa: Empresa,
    nfse_result: dict[str, Any],
    *,
    destinatario_extra: Optional[str] = None,
) -> bool:
    """
    Envia a NFS-e emitida por e-mail para o cliente.

    Args:
        empresa: dados da empresa tomadora (planilha) — usa empresa.email
        nfse_result: dict retornado por ResultadoEmissao.to_dict() com:
            - numero_nfse: número da NFS-e
            - xml_enviado_path: caminho do XML da DPS enviada
            - xml_retorno_path: caminho do XML de retorno do webservice
            - pdf_path: caminho do PDF (hoje sempre None — não geramos mais PDF
              próprio; o DANFSE oficial virá do ISSnet via ConsultarUrlNfse)
            - codigo_verificacao: código de verificação da NFS-e
        destinatario_extra: e-mail adicional para enviar cópia (CC)

    Returns:
        True se o e-mail foi enviado com sucesso, False caso contrário.
        Falhas de e-mail NÃO levantam exceção — apenas logam o erro.
    """
    # Verifica configuração SMTP
    if not _smtp_configurado():
        logger.warning(
            f"SMTP não configurado — NFS-e de {empresa.razao_social} "
            f"(Nº {nfse_result.get('numero_nfse', '?')}) NÃO enviada por e-mail. "
            f"Configure SMTP_HOST, SMTP_USUARIO e SMTP_SENHA no .env"
        )
        return False

    # Verifica e-mail do destinatário
    email_destino = empresa.email
    if not email_destino:
        logger.warning(
            f"Empresa {empresa.razao_social} ({empresa.cnpj}) sem e-mail cadastrado — "
            f"NFS-e Nº {nfse_result.get('numero_nfse', '?')} não enviada"
        )
        return False

    numero_nfse = nfse_result.get("numero_nfse", "?")
    codigo_verif = nfse_result.get("codigo_verificacao", "")

    # Monta o e-mail
    msg = MIMEMultipart()
    msg["From"] = f"{settings.smtp_remetente_nome} <{_remetente()}>"
    msg["To"] = email_destino
    msg["Subject"] = (
        f"NFS-e Nº {numero_nfse} — {settings.nfse_razao_social_prestador}"
    )

    if destinatario_extra:
        msg["Cc"] = destinatario_extra

    # Corpo do e-mail (HTML)
    corpo_html = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
<p>Prezado(a),</p>

<p>Segue em anexo a <strong>Nota Fiscal de Serviço Eletrônica (NFS-e)</strong>
referente aos serviços prestados por <strong>{settings.nfse_razao_social_prestador}</strong>.</p>

<table style="border-collapse: collapse; margin: 16px 0;">
  <tr>
    <td style="padding: 4px 12px; font-weight: bold;">Número da NFS-e:</td>
    <td style="padding: 4px 12px;">{numero_nfse}</td>
  </tr>
  <tr>
    <td style="padding: 4px 12px; font-weight: bold;">Tomador:</td>
    <td style="padding: 4px 12px;">{empresa.razao_social}</td>
  </tr>
  <tr>
    <td style="padding: 4px 12px; font-weight: bold;">CNPJ Tomador:</td>
    <td style="padding: 4px 12px;">{empresa.cnpj}</td>
  </tr>
  {"<tr><td style='padding: 4px 12px; font-weight: bold;'>Código de Verificação:</td>"
   f"<td style='padding: 4px 12px;'>{codigo_verif}</td></tr>" if codigo_verif else ""}
</table>

<p>O(s) arquivo(s) XML da NFS-e segue(m) em anexo.</p>

<p style="font-size: 0.9em; color: #666;">
Este e-mail foi gerado automaticamente pelo sistema de faturamento da
{settings.nfse_razao_social_prestador}. Em caso de dúvidas, entre em contato
respondendo a este e-mail.
</p>
</body>
</html>
"""
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    # Anexa os XMLs disponíveis. O XML é o entregável principal: não geramos mais
    # PDF próprio, então o envio NÃO depende de PDF — o corpo do e-mail já traz
    # número + código de verificação. Se houver pdf_path (futuro), também anexa.
    anexos_ok = 0
    xml_enviado = nfse_result.get("xml_enviado_path")
    if xml_enviado:
        nome_xml = f"NFS-e_{numero_nfse}_DPS.xml"
        if _anexar_arquivo(msg, Path(xml_enviado), nome_xml):
            anexos_ok += 1

    xml_retorno = nfse_result.get("xml_retorno_path")
    if xml_retorno:
        nome_ret = f"NFS-e_{numero_nfse}_retorno.xml"
        if _anexar_arquivo(msg, Path(xml_retorno), nome_ret):
            anexos_ok += 1

    # TODO ConsultarUrlNfse: hoje pdf_path é sempre None (PDF próprio removido).
    # Quando a consulta da nota no ISSnet (ConsultarUrlNfse) for implementada, ela
    # deverá preencher pdf_path (ou uma URL no corpo) e este bloco anexa o PDF oficial.
    pdf_path = nfse_result.get("pdf_path")
    if pdf_path:
        nome_pdf = f"NFS-e_{numero_nfse}.pdf"
        if _anexar_arquivo(msg, Path(pdf_path), nome_pdf):
            anexos_ok += 1

    if anexos_ok == 0:
        logger.warning(
            f"Nenhum arquivo de NFS-e encontrado para anexar ao e-mail "
            f"(NFS-e Nº {numero_nfse}, empresa {empresa.razao_social})"
        )
        # Envia mesmo assim — o corpo do e-mail já tem número + código de verificação

    # Envia
    destinatarios = [email_destino]
    if destinatario_extra:
        destinatarios.append(destinatario_extra)

    try:
        if settings.smtp_usar_tls:
            # STARTTLS na porta 587
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as servidor:
                servidor.ehlo()
                servidor.starttls()
                servidor.ehlo()
                servidor.login(settings.smtp_usuario, settings.smtp_senha)
                servidor.sendmail(_remetente(), destinatarios, msg.as_string())
        else:
            # SSL direto na porta 465
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as servidor:
                servidor.login(settings.smtp_usuario, settings.smtp_senha)
                servidor.sendmail(_remetente(), destinatarios, msg.as_string())

        logger.info(
            f"📧 NFS-e Nº {numero_nfse} enviada por e-mail para {email_destino} "
            f"({empresa.razao_social}) — {anexos_ok} anexo(s)"
        )
        return True

    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            f"Falha de autenticação SMTP ao enviar NFS-e Nº {numero_nfse}: {exc}. "
            f"Verifique SMTP_USUARIO e SMTP_SENHA no .env"
        )
    except smtplib.SMTPException as exc:
        logger.error(
            f"Erro SMTP ao enviar NFS-e Nº {numero_nfse} para {email_destino}: {exc}"
        )
    except Exception as exc:
        logger.error(
            f"Erro inesperado ao enviar e-mail NFS-e Nº {numero_nfse}: {exc}"
        )

    return False
