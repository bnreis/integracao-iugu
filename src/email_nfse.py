"""
Módulo de envio de NFS-e por e-mail.

Envia o XML da NFS-e emitida como anexo para o e-mail do cliente (tomador),
acompanhado de um template HTML responsivo (CSS inline) com os dados da nota.

Usado em dois fluxos — AMBOS passam pela mesma função `enviar_nfse_email`,
garantindo template idêntico:
  1. PAGAMENTO da fatura (webhook_server.py → processar_pagamento, auto-envio)
  2. Reenvio manual pelo painel/app (api_routes.py → reenviar_nfse_email)
  (e também a emissão na CRIAÇÃO da fatura, scheduled_invoices.py)

Requisitos:
  - Configurar SMTP_HOST, SMTP_USUARIO e SMTP_SENHA no .env
  - O remetente é financeiro@megasuporte.com (settings.smtp_remetente_email);
    o SMTP precisa autenticar como esse endereço (ou um relay que o autorize).
  - O e-mail do destinatário vem do campo `email` do objeto Empresa (tomador).
  - A logo da assinatura (assets/logo_megasuporte.png) é embutida via CID se
    existir; se não existir, a assinatura usa só texto (sem quebrar o envio).

Se as configurações SMTP não estiverem preenchidas, loga um aviso e retorna
sem erro (o fluxo principal não é interrompido por falha de e-mail).
"""
from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .config import PROJECT_ROOT, settings
from .iugu_empresas import Empresa

# Caminho da logo da assinatura. Embutida como imagem inline (CID) SE existir.
LOGO_PATH = PROJECT_ROOT / "assets" / "logo_megasuporte.png"
# Content-ID usado para referenciar a logo inline no HTML (<img src="cid:...">).
_LOGO_CID = "logo_megasuporte"

# Dados fixos da assinatura (rodapé) — aprovados pelo Bruno.
_PRESTADOR_NOME = "MEGASUPORTE SERVIÇOS DE TI"
_FINANCEIRO_EMAIL = "financeiro@megasuporte.com"
_ENDERECO_LINHA_1 = "SHN Quadra 01 Conjunto A Bloco A Ed. Le Quartier"
_ENDERECO_LINHA_2 = "5º andar, sala 523, Brasília-DF, Cep 70.701-000"
# Página oficial de verificação de autenticidade da NFS-e do DF.
_PORTAL_DANFSE = "https://iss.fazenda.df.gov.br/online/NotaDigital/VerificaAutenticidade.aspx"
# Inscrição Municipal (CF/DF) da MEGASUPORTE — pedida na página de verificação.
_INSCRICAO_MUNICIPAL = "0796481500161"


def _smtp_configurado() -> bool:
    """Verifica se as configurações mínimas de SMTP estão preenchidas."""
    return bool(settings.smtp_host and settings.smtp_usuario and settings.smtp_senha)


def _remetente() -> str:
    """Retorna o e-mail do remetente (campo dedicado ou o usuário SMTP)."""
    return settings.smtp_remetente_email or settings.smtp_usuario


def _formatar_valor_brl(valor: Any) -> Optional[str]:
    """Formata um valor numérico (reais) como 'R$ 1.234,56'. Retorna None se inválido."""
    if valor is None or valor == "":
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        # Já veio formatado como string (ex.: "3.150,00") — devolve como está.
        return str(valor)
    # Formato BR: milhar com ponto, decimal com vírgula.
    inteiro = f"{numero:,.2f}"  # ex.: "3,150.00"
    inteiro = inteiro.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {inteiro}"


def _extrair_nfse_do_retorno(caminho_xml: Path) -> Optional[bytes]:
    """
    Lê o XML de retorno do webservice e tenta extrair só o elemento da NFS-e
    (CompNfse/Nfse), descartando o envelope SOAP/resposta. Se não conseguir
    isolar o nó, devolve o conteúdo inteiro do arquivo (fallback seguro).

    Retorna os bytes do XML a anexar, ou None se o arquivo não puder ser lido.
    """
    try:
        conteudo = caminho_xml.read_bytes()
    except Exception as exc:
        logger.warning(f"Não foi possível ler o XML de retorno {caminho_xml}: {exc}")
        return None

    # Tenta isolar <CompNfse> (ABRASF) ou <Nfse> (padrão nacional) via lxml.
    # Qualquer falha de parse cai no fallback: anexa o retorno inteiro.
    try:
        from lxml import etree

        raiz = etree.fromstring(conteudo)
        for tag in ("CompNfse", "Nfse"):
            # Busca ignorando namespace (local-name) — robusto a prefixos variados.
            encontrados = raiz.xpath(f"//*[local-name()='{tag}']")
            if encontrados:
                no = encontrados[0]
                return etree.tostring(no, encoding="utf-8", xml_declaration=True)
    except Exception as exc:
        logger.debug(f"Não foi possível isolar CompNfse/Nfse ({exc}); anexando retorno inteiro")

    return conteudo


def _anexar_xml_nfse(msg: MIMEMultipart, dados: dict[str, Any], numero_nfse: str) -> bool:
    """
    Anexa o XML da NFS-e oficial (vindo do xml_retorno_path). Cai para o
    xml_enviado_path apenas se o retorno não existir. Retorna True se anexou.
    """
    # Preferência: o XML de RETORNO contém a NFS-e oficial (número + código).
    xml_retorno = dados.get("xml_retorno_path")
    if xml_retorno:
        caminho = Path(xml_retorno)
        if caminho.exists():
            payload = _extrair_nfse_do_retorno(caminho)
            if payload:
                parte = MIMEApplication(payload, _subtype="xml")
                nome = f"NFS-e_{numero_nfse}.xml"
                parte["Content-Disposition"] = f'attachment; filename="{nome}"'
                parte.set_type("application/xml")
                msg.attach(parte)
                return True
        else:
            logger.warning(f"xml_retorno_path não encontrado no disco: {caminho}")

    # Fallback: o DPS/RPS enviado (não é a nota oficial, mas é melhor que nada).
    xml_enviado = dados.get("xml_enviado_path")
    if xml_enviado:
        caminho = Path(xml_enviado)
        if caminho.exists():
            try:
                parte = MIMEApplication(caminho.read_bytes(), _subtype="xml")
                nome = f"NFS-e_{numero_nfse}_DPS.xml"
                parte["Content-Disposition"] = f'attachment; filename="{nome}"'
                parte.set_type("application/xml")
                msg.attach(parte)
                return True
            except Exception as exc:
                logger.warning(f"Falha ao anexar XML enviado {caminho}: {exc}")

    return False


def _bloco_logo_html() -> str:
    """Retorna a tag <img> da logo (CID) se o arquivo existir, senão string vazia."""
    if LOGO_PATH.exists():
        return (
            f'<img src="cid:{_LOGO_CID}" alt="{_PRESTADOR_NOME}" '
            f'style="max-height: 56px; margin-bottom: 8px;" />'
        )
    return ""


def _montar_html(
    *,
    numero_nfse: str,
    codigo_verif: str,
    data_emissao: Optional[str],
    valor_fmt: Optional[str],
) -> str:
    """Monta o corpo HTML do e-mail (responsivo simples, CSS inline)."""
    # Linhas opcionais da tabela: só renderiza se houver o dado.
    linha_codigo = (
        f"""
      <tr>
        <td style="padding: 8px 14px; font-weight: bold; border: 1px solid #e0e0e0; background: #f7f7f7;">Código de verificação</td>
        <td style="padding: 8px 14px; border: 1px solid #e0e0e0;">{codigo_verif}</td>
      </tr>"""
        if codigo_verif
        else ""
    )
    linha_data = (
        f"""
      <tr>
        <td style="padding: 8px 14px; font-weight: bold; border: 1px solid #e0e0e0; background: #f7f7f7;">Data de emissão</td>
        <td style="padding: 8px 14px; border: 1px solid #e0e0e0;">{data_emissao}</td>
      </tr>"""
        if data_emissao
        else ""
    )
    linha_valor = (
        f"""
      <tr>
        <td style="padding: 8px 14px; font-weight: bold; border: 1px solid #e0e0e0; background: #f7f7f7;">Valor dos serviços</td>
        <td style="padding: 8px 14px; border: 1px solid #e0e0e0;">{valor_fmt}</td>
      </tr>"""
        if valor_fmt
        else ""
    )

    return f"""\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
</head>
<body style="margin: 0; padding: 0; background: #f4f4f4;">
  <div style="max-width: 600px; margin: 0 auto; padding: 24px; font-family: Arial, Helvetica, sans-serif; color: #333; font-size: 15px; line-height: 1.5;">

    <p style="margin: 0 0 12px;">Prezados,</p>

    <p style="margin: 0 0 16px;">
      A <strong>{_PRESTADOR_NOME}</strong> emitiu a Nota Fiscal de Serviço Eletrônica (NFS-e)
      referente aos serviços prestados.
    </p>

    <table role="presentation" style="border-collapse: collapse; width: 100%; max-width: 480px; margin: 0 0 18px;">
      <tr>
        <td style="padding: 8px 14px; font-weight: bold; border: 1px solid #e0e0e0; background: #f7f7f7;">Inscrição Municipal (prestador)</td>
        <td style="padding: 8px 14px; border: 1px solid #e0e0e0;">{_INSCRICAO_MUNICIPAL}</td>
      </tr>
      <tr>
        <td style="padding: 8px 14px; font-weight: bold; border: 1px solid #e0e0e0; background: #f7f7f7;">Número da NFS-e</td>
        <td style="padding: 8px 14px; border: 1px solid #e0e0e0;">{numero_nfse}</td>
      </tr>{linha_codigo}{linha_data}{linha_valor}
    </table>

    <p style="margin: 0 0 12px;">
      &#128206; <strong>Anexo:</strong> XML da NFS-e.
    </p>

    <p style="margin: 0 0 16px;">
      &#128279; <strong>PDF oficial / verificar autenticidade:</strong> acesse
      <a href="{_PORTAL_DANFSE}" style="color: #1a5fb4;">{_PORTAL_DANFSE}</a>
      e informe os dados acima (Inscrição Municipal, Número da NFS-e e Código de
      verificação) para visualizar/imprimir a nota.
    </p>

    <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 20px 0;" />

    <!-- Assinatura / rodapé -->
    <div style="font-size: 13px; color: #555;">
      {_bloco_logo_html()}
      <p style="margin: 0 0 2px; font-weight: bold; color: #333;">Financeiro</p>
      <p style="margin: 0 0 2px;">
        <a href="mailto:{_FINANCEIRO_EMAIL}" style="color: #1a5fb4;">{_FINANCEIRO_EMAIL}</a>
      </p>
      <p style="margin: 0 0 2px;">{_ENDERECO_LINHA_1}</p>
      <p style="margin: 0;">{_ENDERECO_LINHA_2}</p>
    </div>

  </div>
</body>
</html>
"""


def montar_email_nfse(
    empresa: Empresa,
    dados: dict[str, Any],
    *,
    destinatario_extra: Optional[str] = None,
) -> tuple[MIMEMultipart, str, list[str]]:
    """
    Monta a mensagem MIME completa (template HTML + logo inline + anexo XML).

    Separada do envio para permitir PREVIEW sem SMTP (scripts/preview_email_nfse.py).

    Args:
        empresa: dados do tomador — usa empresa.razao_social e empresa.email.
        dados: dict com numero_nfse, codigo_verificacao, valor, data_emissao,
            xml_retorno_path / xml_enviado_path.
        destinatario_extra: e-mail adicional em cópia (CC).

    Returns:
        (mensagem MIME, html do corpo, lista de destinatários).
    """
    numero_nfse = str(dados.get("numero_nfse") or "?")
    codigo_verif = str(dados.get("codigo_verificacao") or "")
    data_emissao = dados.get("data_emissao")
    valor_fmt = _formatar_valor_brl(dados.get("valor"))
    razao_tomador = empresa.razao_social or "Cliente"

    # Estrutura MIME canônica para "corpo HTML + logo inline + anexo":
    #   multipart/mixed
    #   ├── multipart/related   (corpo)
    #   │   ├── text/html       (template)
    #   │   └── image/png       (logo inline, CID)
    #   └── application/xml     (anexo NFS-e)
    # Anexo NÃO pode ficar dentro do "related": alguns clientes (Gmail) passam
    # a tratar o anexo como conteúdo principal e somem com o corpo HTML.
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{settings.smtp_remetente_nome} <{_remetente()}>"
    msg["To"] = empresa.email or ""
    msg["Subject"] = f"NFS-e nº {numero_nfse} — {_PRESTADOR_NOME}"
    destinatarios: list[str] = [empresa.email] if empresa.email else []
    if destinatario_extra:
        msg["Cc"] = destinatario_extra
        destinatarios.append(destinatario_extra)

    html = _montar_html(
        numero_nfse=numero_nfse,
        codigo_verif=codigo_verif,
        data_emissao=str(data_emissao) if data_emissao else None,
        valor_fmt=valor_fmt,
    )

    # Corpo = multipart/related (HTML + logo inline referenciada por CID).
    corpo = MIMEMultipart("related")
    corpo.attach(MIMEText(html, "html", "utf-8"))
    if LOGO_PATH.exists():
        try:
            with open(LOGO_PATH, "rb") as f:
                img = MIMEImage(f.read())
            img.add_header("Content-ID", f"<{_LOGO_CID}>")
            img.add_header("Content-Disposition", "inline", filename=LOGO_PATH.name)
            corpo.attach(img)
        except Exception as exc:
            logger.warning(f"Falha ao embutir logo {LOGO_PATH}: {exc}")
    msg.attach(corpo)

    # Anexo: XML da NFS-e oficial, no nível "mixed". Se não houver, envia mesmo
    # assim (warning).
    if not _anexar_xml_nfse(msg, dados, numero_nfse):
        logger.warning(
            f"XML da NFS-e Nº {numero_nfse} não encontrado — e-mail enviado SEM anexo "
            f"(empresa {razao_tomador})"
        )

    return msg, html, destinatarios


def enviar_nfse_email(
    empresa: Empresa,
    nfse_result: dict[str, Any],
    *,
    destinatario_extra: Optional[str] = None,
) -> bool:
    """
    Envia a NFS-e emitida por e-mail para o tomador (mesmo template no auto-envio
    do webhook e no reenvio manual do painel/app).

    Args:
        empresa: dados da empresa tomadora — usa empresa.razao_social e empresa.email.
        nfse_result: dict com os dados da nota. Chaves consumidas:
            - numero_nfse: número da NFS-e
            - codigo_verificacao: código de verificação
            - valor: valor dos serviços em reais (float) — opcional
            - data_emissao: data ISO YYYY-MM-DD — opcional
            - xml_retorno_path: XML de retorno (NFS-e oficial) — anexado de preferência
            - xml_enviado_path: XML do DPS/RPS enviado — fallback de anexo
        destinatario_extra: e-mail adicional para cópia (CC).

    Returns:
        True se o e-mail foi enviado com sucesso, False caso contrário.
        Falhas de e-mail NÃO levantam exceção — apenas logam o erro.
    """
    if not _smtp_configurado():
        logger.warning(
            f"SMTP não configurado — NFS-e de {empresa.razao_social} "
            f"(Nº {nfse_result.get('numero_nfse', '?')}) NÃO enviada por e-mail. "
            f"Configure SMTP_HOST, SMTP_USUARIO e SMTP_SENHA no .env"
        )
        return False

    if not empresa.email:
        logger.warning(
            f"Empresa {empresa.razao_social} ({empresa.cnpj}) sem e-mail cadastrado — "
            f"NFS-e Nº {nfse_result.get('numero_nfse', '?')} não enviada"
        )
        return False

    numero_nfse = nfse_result.get("numero_nfse", "?")
    msg, _html, destinatarios = montar_email_nfse(
        empresa, nfse_result, destinatario_extra=destinatario_extra
    )

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
            f"📧 NFS-e Nº {numero_nfse} enviada por e-mail para {empresa.email} "
            f"({empresa.razao_social})"
        )
        return True

    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            f"Falha de autenticação SMTP ao enviar NFS-e Nº {numero_nfse}: {exc}. "
            f"Verifique SMTP_USUARIO e SMTP_SENHA no .env"
        )
    except smtplib.SMTPException as exc:
        logger.error(
            f"Erro SMTP ao enviar NFS-e Nº {numero_nfse} para {empresa.email}: {exc}"
        )
    except Exception as exc:
        logger.error(
            f"Erro inesperado ao enviar e-mail NFS-e Nº {numero_nfse}: {exc}"
        )

    return False
