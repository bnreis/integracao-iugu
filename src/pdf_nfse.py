"""
Módulo de geração de PDF da NFS-e com design customizado.

Gera um PDF formatado e profissional com:
- Logo da MEGASUPORTE
- Dados da NFS-e (número, série, data, código de verificação)
- Dados do Tomador (CNPJ, razão social, endereço)
- Descrição e valor do serviço
- QR code para validação no portal ISS.net
- Rodapé com informações legais

Requisitos:
- reportlab (para geração de PDF)
- qrcode (para geração de QR code)
- PIL/Pillow (para manipulação de imagens)
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Optional, Any
import io

from loguru import logger

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm, mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    REPORTLAB_DISPONIVEL = True
except ImportError:
    REPORTLAB_DISPONIVEL = False
    logger.warning("reportlab não instalado. Execute: pip install reportlab pillow qrcode")

try:
    import qrcode
    from PIL import Image as PILImage
    QRCODE_DISPONIVEL = True
except ImportError:
    QRCODE_DISPONIVEL = False


# ============================================================================
# Constantes de design
# ============================================================================
CORES = {
    "azul_escuro": colors.HexColor("#003D82"),  # Azul da Nota Control
    "azul_claro": colors.HexColor("#0066CC"),
    "verde": colors.HexColor("#2E7D32"),
    "cinza": colors.HexColor("#666666"),
    "cinza_claro": colors.HexColor("#F5F5F5"),
    "branco": colors.white,
    "preto": colors.black,
}

# Diretório para assets (logo, etc.)
ASSETS_DIR = Path(__file__).parent.parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True, parents=True)

LOGO_PATH = ASSETS_DIR / "logo_megasuporte.png"


def _gerar_qrcode(dados: str) -> io.BytesIO:
    """Gera um QR code a partir de uma string de dados.

    Args:
        dados: String para codificar no QR code (URL ou texto)

    Returns:
        BytesIO com imagem PNG do QR code
    """
    if not QRCODE_DISPONIVEL:
        logger.warning("qrcode não disponível — QR code não será incluído no PDF")
        return None

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=5,
            border=2,
        )
        qr.add_data(dados)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        return img_bytes
    except Exception as exc:
        logger.warning(f"Falha ao gerar QR code: {exc}")
        return None


def _adicionar_logo(pdf_canvas: canvas.Canvas, x: float, y: float, largura: float = 2*cm) -> None:
    """Adiciona o logo ao PDF.

    Se o arquivo de logo não existir, desenha um retângulo com texto.
    """
    try:
        if LOGO_PATH.exists():
            pdf_canvas.drawImage(
                str(LOGO_PATH),
                x, y,
                width=largura,
                height=largura * 0.75,
                preserveAspectRatio=True
            )
            logger.debug(f"Logo adicionado do arquivo: {LOGO_PATH}")
        else:
            # Logo em texto simples (placeholder)
            pdf_canvas.setFont("Helvetica-Bold", 14)
            pdf_canvas.setFillColor(CORES["azul_escuro"])
            pdf_canvas.drawString(x, y, "MEGASUPORTE")
            pdf_canvas.setFont("Helvetica", 8)
            pdf_canvas.setFillColor(CORES["cinza"])
            pdf_canvas.drawString(x, y - 0.3*cm, "Serviços de TI")
            logger.debug("Logo em texto adicionado (arquivo não encontrado)")
    except Exception as exc:
        logger.warning(f"Falha ao adicionar logo: {exc}")


def gerar_pdf_nfse(
    pdf_path: Path,
    numero_nfse: str,
    serie: str,
    data_emissao: str,
    codigo_verificacao: str,
    tomador_nome: str,
    tomador_cnpj: str,
    tomador_endereco: str,
    descricao_servico: str,
    valor_servico: str,
    aliquota_iss: float,
    prestador_nome: str,
    prestador_cnpj: str,
    url_validacao: Optional[str] = None,
    **kwargs
) -> bool:
    """Gera um PDF customizado da NFS-e.

    Args:
        pdf_path: Caminho onde salvar o PDF
        numero_nfse: Número da NFS-e
        serie: Série da NFS-e
        data_emissao: Data de emissão (formato YYYY-MM-DD)
        codigo_verificacao: Código de verificação
        tomador_nome: Razão social do tomador
        tomador_cnpj: CNPJ do tomador
        tomador_endereco: Endereço completo do tomador
        descricao_servico: Descrição do serviço
        valor_servico: Valor do serviço (string formatada "R$ 1.234,56")
        aliquota_iss: Alíquota ISS (ex: 2.0)
        prestador_nome: Nome do prestador
        prestador_cnpj: CNPJ do prestador
        url_validacao: URL para validar a NFS-e no portal ISS.net

    Returns:
        True se sucesso, False se falha
    """
    if not REPORTLAB_DISPONIVEL:
        logger.error("reportlab não disponível — impossível gerar PDF")
        return False

    try:
        pdf_path = Path(pdf_path)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)

        # Criar documento
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=1.5*cm,
            bottomMargin=1.5*cm,
        )

        # Lista de elementos para o PDF
        elements = []

        # Estilos
        styles = getSampleStyleSheet()
        titulo_style = ParagraphStyle(
            'TituloNFSe',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=CORES["azul_escuro"],
            spaceAfter=0.3*cm,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
        )

        heading_style = ParagraphStyle(
            'Heading',
            parent=styles['Heading2'],
            fontSize=11,
            textColor=CORES["azul_escuro"],
            spaceAfter=0.2*cm,
            spaceBefore=0.3*cm,
            fontName='Helvetica-Bold',
            borderPadding=5,
            borderColor=CORES["azul_claro"],
            borderWidth=1,
            backColor=CORES["cinza_claro"],
        )

        normal_style = ParagraphStyle(
            'Normal',
            parent=styles['Normal'],
            fontSize=9,
            textColor=CORES["preto"],
            spaceAfter=0.1*cm,
        )

        # ====== CABEÇALHO ======
        # Logo + Empresa
        logo_empresa_data = [
            ["", "", ""],
        ]
        logo_empresa_table = Table(logo_empresa_data, colWidths=[2*cm, 10*cm, 4*cm])
        logo_empresa_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ]))
        elements.append(logo_empresa_table)

        # Título
        elements.append(Paragraph("NOTA FISCAL DE SERVIÇO ELETRÔNICA", titulo_style))
        elements.append(Spacer(1, 0.2*cm))

        # ====== DADOS DA NFS-e ======
        elements.append(Paragraph("Informações da NFS-e", heading_style))

        nfse_data = [
            ["Número", numero_nfse, "Série", serie],
            ["Data de Emissão", data_emissao, "Código de Verificação", codigo_verificacao],
        ]
        nfse_table = Table(nfse_data, colWidths=[2.5*cm, 3*cm, 2.5*cm, 3*cm])
        nfse_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), CORES["cinza_claro"]),
            ('BACKGROUND', (2, 0), (2, -1), CORES["cinza_claro"]),
            ('TEXTCOLOR', (0, 0), (-1, -1), CORES["preto"]),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, CORES["cinza_claro"]]),
        ]))
        elements.append(nfse_table)
        elements.append(Spacer(1, 0.3*cm))

        # ====== DADOS DO PRESTADOR ======
        elements.append(Paragraph("Prestador de Serviço", heading_style))

        prestador_text = f"<b>{prestador_nome}</b><br/>CNPJ: {prestador_cnpj}"
        elements.append(Paragraph(prestador_text, normal_style))
        elements.append(Spacer(1, 0.3*cm))

        # ====== DADOS DO TOMADOR ======
        elements.append(Paragraph("Tomador de Serviço", heading_style))

        tomador_text = f"<b>{tomador_nome}</b><br/>CNPJ: {tomador_cnpj}<br/>{tomador_endereco}"
        elements.append(Paragraph(tomador_text, normal_style))
        elements.append(Spacer(1, 0.3*cm))

        # ====== DADOS DO SERVIÇO ======
        elements.append(Paragraph("Serviço Prestado", heading_style))

        servico_data = [
            ["Descrição", "Valor", "Alíquota ISS"],
            [descricao_servico, valor_servico, f"{aliquota_iss}%"],
        ]
        servico_table = Table(servico_data, colWidths=[8*cm, 2.5*cm, 2.5*cm])
        servico_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), CORES["azul_escuro"]),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ALIGN', (1, 1), (-1, 1), 'RIGHT'),
        ]))
        elements.append(servico_table)
        elements.append(Spacer(1, 0.3*cm))

        # ====== QR CODE ======
        if url_validacao and QRCODE_DISPONIVEL:
            elements.append(Paragraph("Validação da NFS-e", heading_style))
            qrcode_img = _gerar_qrcode(url_validacao)
            if qrcode_img:
                elements.append(Image(qrcode_img, width=3*cm, height=3*cm))
                elements.append(Spacer(1, 0.2*cm))
                elements.append(Paragraph(
                    f"<align=center><font size=8>Escaneie o código QR para validar esta NFS-e</font></align>",
                    normal_style
                ))
            elements.append(Spacer(1, 0.3*cm))

        # ====== RODAPÉ ======
        elements.append(Spacer(1, 0.5*cm))
        rodape = (
            f"<align=center><font size=7 color='{CORES['cinza']}'>Este documento é uma representação visual "
            f"da Nota Fiscal de Serviço Eletrônica. A validade legal da NFS-e está no portal ISS.net.<br/>"
            f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}</font></align>"
        )
        elements.append(Paragraph(rodape, normal_style))

        # Gerar PDF
        doc.build(elements)
        logger.info(f"📄 PDF NFS-e gerado com sucesso: {pdf_path}")
        return True

    except Exception as exc:
        logger.error(f"Erro ao gerar PDF NFS-e: {exc}", exc_info=True)
        return False
