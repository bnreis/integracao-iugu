"""
Emissão de NFS-e no Distrito Federal — Padrão Nacional CGNFS-e.

Este módulo implementa o fluxo completo:
    1. Montar a DPS (Declaração de Prestação de Serviços) em XML usando `nfelib`
    2. Assinar o XML com certificado digital A1/A3 (`erpbrasil.assinatura`)
    3. Enviar para o webservice do ISS DF (SOAP ou REST, configurável)
    4. Parsear o retorno (número NFS-e + código de verificação)
    5. Arquivar XML enviado + XML retornado em disco

Fonte de dados do tomador:
- Identificação (CNPJ, razão social, e-mail, IM): do objeto Empresa (planilha)
- Dados do serviço (código, descrição, alíquota): do objeto Empresa
- ENDEREÇO do tomador: da FATURA Iugu (campos payer_address_*), NÃO da planilha.

Observação sobre o ambiente:
- `NFSE_AMBIENTE=homologacao` ou `producao` define qual URL usar
- `NFSE_WS_URL_HOMOLOGACAO` / `NFSE_WS_URL_PRODUCAO` devem ser obtidas junto
  ao Nota Control (suporte.df@notacontrol.com.br)
- `NFSE_WS_PROTOCOLO=soap` ou `rest` — o DF tipicamente usa SOAP

Referências:
- https://github.com/akretion/nfelib (bindings XML)
- https://github.com/erpbrasil/erpbrasil.assinatura (assinatura A1)
- https://www.gov.br/nfse/pt-br (portal nacional CGNFS-e)
- https://iss.fazenda.df.gov.br/online (portal DF)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger

from .config import get_nfse_endpoint, settings
from .iugu_empresas import Empresa, format_cents_to_br

# -----------------------------------------------------------------------------
# Imports condicionais (libs pesadas) — validados na hora da emissão
# -----------------------------------------------------------------------------
try:
    from nfelib.nfse.bindings.v1_0.dps_v1_00 import Dps
    from nfelib.nfse.bindings.v1_0.nfse_v1_00 import Nfse
    from nfelib.nfse.bindings.v1_0 import tipos_complexos_v1_00 as tc
    from nfelib.nfse.bindings.v1_0 import tipos_simples_v1_00 as ts
    NFELIB_DISPONIVEL = True
except ImportError:  # pragma: no cover
    NFELIB_DISPONIVEL = False
    logger.warning(
        "nfelib não instalado. Execute: pip install nfelib erpbrasil.assinatura"
    )

try:
    from erpbrasil.assinatura.certificado import Certificado as _AssinaturaCertificado
    from erpbrasil.assinatura.assinatura import Assinatura as _Assinatura
    ERPBRASIL_DISPONIVEL = True
except ImportError:  # pragma: no cover
    ERPBRASIL_DISPONIVEL = False


# -----------------------------------------------------------------------------
# Parser XML seguro (defesa em profundidade contra XXE / billion laughs)
# -----------------------------------------------------------------------------
# Reutilizado em qualquer parsing de XML de origem externa (resposta SOAP do
# webservice ABRASF/ISSnet) e também no pós-processamento do XML assinado antes
# do envio. lxml por padrão JÁ é mais restritivo que xml.etree em relação a
# entidades externas, mas mantemos os flags explícitos para que a intenção seja
# obvia em revisões futuras e para garantir o comportamento desejado em qualquer
# build do libxml2:
#   - resolve_entities=False  → não expande entidades (mitiga billion laughs)
#   - no_network=True         → bloqueia acesso a recursos externos por URL
#   - load_dtd=False          → ignora DTD inline
#   - dtd_validation=False    → não valida via DTD
#   - huge_tree=False         → limites de profundidade/tamanho do libxml2
# Lazy-init no primeiro uso para não falhar caso lxml não esteja disponível
# durante import do módulo (ambientes de bootstrap/CLI).
_SAFE_XML_PARSER = None


def _get_safe_xml_parser():
    """Retorna o parser XML seguro do módulo (lazy-init na primeira chamada)."""
    global _SAFE_XML_PARSER
    if _SAFE_XML_PARSER is None:
        from lxml import etree

        _SAFE_XML_PARSER = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            dtd_validation=False,
            huge_tree=False,
        )
    return _SAFE_XML_PARSER


# =============================================================================
# Dataclasses de retorno
# =============================================================================
@dataclass
class DadosServico:
    codigo_servico: str
    descricao: str
    valor_cents: int
    aliquota_iss: float


@dataclass
class ResultadoEmissao:
    sucesso: bool
    numero_nfse: Optional[str] = None
    codigo_verificacao: Optional[str] = None
    xml_enviado_path: Optional[Path] = None
    xml_retorno_path: Optional[Path] = None
    # PDF do DANFSE: NÃO geramos mais PDF próprio (reportlab removido). A chave
    # permanece no dict de retorno para não quebrar consumidores (api_routes,
    # email_nfse), mas hoje fica sempre None.
    # TODO ConsultarUrlNfse: preencher com a URL/PDF oficial do ISSnet quando a
    # consulta da nota emitida (ConsultarUrlNfse) for implementada.
    pdf_path: Optional[Path] = None
    mensagem_erro: Optional[str] = None
    mensagens: list[str] = field(default_factory=list)
    ambiente: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sucesso": self.sucesso,
            "numero_nfse": self.numero_nfse,
            "codigo_verificacao": self.codigo_verificacao,
            "xml_enviado_path": str(self.xml_enviado_path) if self.xml_enviado_path else None,
            "xml_retorno_path": str(self.xml_retorno_path) if self.xml_retorno_path else None,
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "mensagem_erro": self.mensagem_erro,
            "mensagens": self.mensagens,
            "ambiente": self.ambiente,
        }


# =============================================================================
# DISPATCHER POR PROTOCOLO (ADR-0005, Parte A)
# =============================================================================
# `emitir_nfse` é a fronteira pública chamada pelo webhook_server. A partir do
# ADR-0005 ela apenas SELECIONA o backend de emissão conforme settings.nfse_padrao
# e delega — sem nenhuma lógica de protocolo aqui. Isso preserva 100% o
# comportamento atual (caminho "nacional"/DPS) e cria o ponto de extensão para o
# backend ABRASF 2.04 (RPS), implementado na Parte B.
#
# Contrato preservado (do qual processar_pagamento e o WEB-011 dependem):
#   - assinatura: async def emitir_nfse(invoice, empresa)
#   - retorno: dict com a chave "sucesso" (ResultadoEmissao.to_dict())
# =============================================================================
async def emitir_nfse(invoice: dict[str, Any], empresa: Empresa) -> dict[str, Any]:
    """Emite uma NFS-e no DF, despachando para o backend do protocolo configurado.

    Lê `settings.nfse_padrao`:
        - "nacional"  → `_emitir_nacional` (Padrão Nacional / DPS v1.01 — atual)
        - "abrasf204" → `_emitir_abrasf204` (RPS ABRASF 2.04 — stub na Parte A)

    Args:
        invoice: payload completo da fatura da Iugu (tem payer_address_*, payer_name, etc.)
        empresa: dados cadastrais da empresa tomadora.

    Returns:
        Resultado em dict (ResultadoEmissao.to_dict()), idêntico em ambos os backends.
    """
    padrao = (settings.nfse_padrao or "nacional").lower()
    if padrao == "abrasf204":
        return await _emitir_abrasf204(invoice, empresa)
    # Default e fallback: caminho nacional (comportamento de produção atual).
    return await _emitir_nacional(invoice, empresa)


# =============================================================================
# ADR-0005 Parte B — BACKEND ABRASF 2.04 (RPS, ISSnet DF)
# =============================================================================
# Backend de emissão para o webservice ABRASF 2.04 do DF (ISSnet), operação
# síncrona `GerarNfse` (1 RPS série 3 → 1 NFS-e). Espelha o plumbing do backend
# nacional (assinatura A1, mTLS httpx, arquivamento, PDF, lock do contador), mas
# monta o XML RPS conforme o XSD ABRASF 2.04 (schema nfse v2-04.xsd) — SEM IBSCBS
# e SEM patch v1.00→v1.01.
#
# Devolve EXATAMENTE o mesmo contrato (ResultadoEmissao.to_dict()), com a chave
# "sucesso", para o webhook (processar_pagamento / WEB-011) não perceber diferença.
# Em rejeição retorna sucesso=False SEM levantar exceção (rejeição terminal).
#
# Namespaces / SOAPAction como CONSTANTES no topo da seção: o ISSnet DF pode
# divergir do genérico ABRASF; centralizar facilita ajuste após validação no
# canal integracao.df@notacontrol.com.br.
# =============================================================================

# Namespace do SCHEMA dos dados (RPS, GerarNfseEnvio, GerarNfseResposta). Vem do
# `targetNamespace` do XSD ABRASF 2.04 (schema nfse v2-04.xsd).
ABRASF_SCHEMA_NS = "http://www.abrasf.org.br/nfse.xsd"
# Namespace do SERVIÇO SOAP (operações, nfseCabecMsg/nfseDadosMsg). Vem do WSDL
# (targetNamespace="http://nfse.abrasf.org.br"). ⚠️ NÃO confundir com o do schema.
ABRASF_SERVICE_NS = "http://nfse.abrasf.org.br"
# Operação síncrona escolhida (1 RPS → 1 NFS-e), alinhada a "1 fatura paga → 1 NFS-e".
ABRASF_OPERACAO = "GerarNfse"
# SOAPAction da operação (conforme binding document/literal do WSDL).
ABRASF_SOAP_ACTION = f"{ABRASF_SERVICE_NS}/{ABRASF_OPERACAO}"
# Versão do leiaute ABRASF informada no cabeçalho (tsVersao = "[1-9][0-9]?\.[0-9]{2}").
ABRASF_VERSAO_DADOS = "2.04"


async def _emitir_abrasf204(invoice: dict[str, Any], empresa: Empresa) -> dict[str, Any]:
    """Emite uma NFS-e no DF via ABRASF 2.04 (RPS série 3, ISSnet) para uma fatura paga.

    Fluxo (espelha o backend nacional, trocando só a montagem/protocolo do XML):
        1. Valida config + valor
        2. Monta o RPS (GerarNfseEnvio > Rps > InfDeclaracaoPrestacaoServico) — XSD 2.04
        3. Assina o RPS (XMLDSig enveloped, RSA-SHA1/SHA1/C14N) referenciando o Id
        4. Envelopa em SOAP (nfseCabecMsg + nfseDadosMsg), operação GerarNfse
        5. Envia via httpx com mTLS ao endpoint ABRASF (homologação/produção)
        6. Parseia GerarNfseResposta (Numero/CodigoVerificacao ou ListaMensagemRetorno)
        7. Arquiva envio+retorno e gera PDF (e-mail é disparado por processar_pagamento)

    Returns:
        Resultado em dict (ResultadoEmissao.to_dict()), idêntico ao backend nacional.
    """
    if not ERPBRASIL_DISPONIVEL:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro="Dependência ausente. Rode: pip install erpbrasil.assinatura",
        ).to_dict()

    # 1. Validação básica (mesma do nacional — config + valor da fatura)
    total_cents = int(
        invoice.get("total_paid_cents") or invoice.get("total_cents") or 0
    )
    if total_cents <= 0:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro="Valor da fatura inválido ou zero",
        ).to_dict()

    problemas = _validar_configuracao(empresa)
    if problemas:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro="Configuração incompleta: " + "; ".join(problemas),
        ).to_dict()

    # 2. Dados do serviço (prioridade: empresa > resumo de itens > default .env)
    servico = DadosServico(
        codigo_servico=empresa.codigo_servico or settings.nfse_codigo_servico_padrao,
        descricao=empresa.descricao_servico or _resumir_itens(invoice) or settings.nfse_descricao_servico_padrao,
        valor_cents=total_cents,
        aliquota_iss=empresa.aliquota_iss or settings.nfse_aliquota_iss_padrao,
    )
    endereco_tomador = extrair_endereco_tomador(invoice)

    logger.info(
        f"[NFS-e ABRASF 2.04] Iniciando emissão: tomador={empresa.razao_social} "
        f"valor={format_cents_to_br(total_cents)} "
        f"serviço={servico.codigo_servico} "
        f"série RPS={settings.nfse_serie_rps} "
        f"ambiente={settings.nfse_ambiente}"
        f"{' [DRY-RUN]' if settings.nfse_dry_run else ''}"
    )

    resultado = ResultadoEmissao(sucesso=False, ambiente=settings.nfse_ambiente)

    # 3. Montar o RPS (GerarNfseEnvio) e assinar
    try:
        numero_rps = _proximo_numero_rps()
        xml_envio, rps_id = _montar_xml_rps_abrasf(
            empresa=empresa,
            servico=servico,
            endereco_tomador=endereco_tomador,
            invoice=invoice,
            numero_rps=numero_rps,
        )
    except Exception as exc:
        logger.exception("Falha ao montar RPS ABRASF 2.04")
        resultado.mensagem_erro = f"Erro ao montar RPS: {exc}"
        return resultado.to_dict()

    try:
        # No GerarNfse assina-se o RPS (InfDeclaracaoPrestacaoServico). A Signature
        # fica como irmã de InfDeclaracaoPrestacaoServico dentro de <Rps>, conforme
        # tcDeclaracaoPrestacaoServico (InfDeclaracaoPrestacaoServico + dsig:Signature).
        xml_assinado = _assinar_xml(xml_envio, rps_id)
        # erpbrasil.assinatura insere a <Signature> no nó RAIZ (GerarNfseEnvio),
        # como irmã de <Rps>. O XSD ABRASF 2.04 (tcDeclaracaoPrestacaoServico)
        # exige a Signature como FILHA de <Rps> (irmã de InfDeclaracaoPrestacaoServico).
        # Reposicionamos antes de envelopar/enviar — sem isso o ISSnet rejeita com E160.
        xml_assinado = _reposicionar_signature_dentro_de_rps(xml_assinado)
    except Exception as exc:
        logger.exception("Falha ao assinar RPS ABRASF 2.04")
        resultado.mensagem_erro = f"Erro ao assinar XML: {exc}"
        return resultado.to_dict()

    # Arquiva o RPS assinado (conteúdo interno, sem envelope SOAP) — diagnóstico
    try:
        _arquivar(xml_assinado, prefix=f"rps_{numero_rps}", suffix="rps_assinado")
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar RPS assinado: {exc}")

    # 4 + DRY-RUN. Monta o envelope SOAP sempre (para arquivar), mas só envia se
    # dry-run estiver desligado — igual ao backend nacional.
    envelope = _envelopar_soap_abrasf(xml_assinado)

    if settings.nfse_dry_run:
        try:
            resultado.xml_enviado_path = _arquivar(
                envelope, prefix=f"rps_{numero_rps}", suffix="envelope_dryrun"
            )
        except Exception as exc:
            logger.warning(f"Não foi possível arquivar envelope (dry-run): {exc}")
        logger.info(
            f"[NFS-e ABRASF 2.04 DRY-RUN] RPS {numero_rps} montado e assinado; "
            f"envelope NÃO enviado (dry-run ativo)."
        )
        resultado.sucesso = True
        resultado.numero_nfse = f"DRY-RUN-RPS-{numero_rps}"
        resultado.ambiente = f"{settings.nfse_ambiente} (dry-run)"
        resultado.mensagens = ["DRY-RUN ABRASF 2.04: RPS montado+assinado, não enviado"]
        # Dry-run gera SÓ o XML (que é o que valida contra o XSD). Não há mais
        # geração de PDF próprio — o DANFSE oficial virá do ISSnet no futuro.
        return resultado.to_dict()

    # 5. Enviar ao webservice ABRASF (mTLS), endpoint conforme ambiente
    endpoint = _abrasf_endpoint()
    if not endpoint:
        resultado.mensagem_erro = (
            "URL do webservice ABRASF 2.04 não configurada. Preencha "
            "NFSE_WS_URL_ABRASF_HOMOLOGACAO / NFSE_WS_URL_ABRASF_PRODUCAO no .env."
        )
        return resultado.to_dict()

    try:
        resposta_xml, status_code = _enviar_soap_abrasf(envelope, endpoint)
    except Exception as exc:
        logger.exception("Falha ao enviar RPS ABRASF ao webservice")
        resultado.mensagem_erro = f"Erro ao enviar ao webservice: {exc}"
        return resultado.to_dict()

    # 6. Arquivar envelope efetivamente enviado + retorno
    try:
        resultado.xml_enviado_path = _arquivar(
            envelope, prefix=f"rps_{numero_rps}", suffix="enviado"
        )
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar envelope enviado: {exc}")
    try:
        resultado.xml_retorno_path = _arquivar(
            resposta_xml, prefix=f"rps_{numero_rps}", suffix="retorno"
        )
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar retorno: {exc}")

    if status_code >= 400:
        resultado.mensagem_erro = (
            f"Webservice ABRASF retornou HTTP {status_code}. "
            f"Veja {resultado.xml_retorno_path}"
        )
        logger.error(
            f"[NFS-e ABRASF 2.04] Webservice respondeu HTTP {status_code} para "
            f"{endpoint} — retorno arquivado em {resultado.xml_retorno_path}"
        )
        return resultado.to_dict()

    # 7. Parsear retorno (GerarNfseResposta — estrutura ABRASF 2.04)
    try:
        info = _parsear_resposta_abrasf(resposta_xml)
        resultado.numero_nfse = info.get("numero_nfse")
        resultado.codigo_verificacao = info.get("codigo_verificacao")
        resultado.mensagens = info.get("mensagens", [])
        resultado.sucesso = info.get("sucesso", False)
        if not resultado.sucesso and not resultado.mensagem_erro:
            resultado.mensagem_erro = (
                info.get("mensagem_erro") or "NFS-e (ABRASF) não foi aprovada pelo ISS DF"
            )
    except Exception as exc:
        logger.exception("Falha ao parsear retorno ABRASF")
        resultado.mensagem_erro = f"Erro ao parsear retorno: {exc}"
        return resultado.to_dict()

    # 8. Em sucesso, apenas loga. Não geramos mais PDF próprio (reportlab removido):
    # o DANFSE oficial será obtido do ISSnet via ConsultarUrlNfse (trabalho futuro).
    # O e-mail é disparado por processar_pagamento e segue só com o XML por enquanto.
    if resultado.sucesso:
        logger.info(
            f"[NFS-e ABRASF 2.04] Emissão OK: número {resultado.numero_nfse} "
            f"código {resultado.codigo_verificacao}"
        )
        # Grava o índice nfse_<invoice_id>.json que a API usa para detectar a nota
        # (listagem/detalhe/dashboard). Resiliente: não derruba a emissão se falhar.
        _gravar_log_nfse(invoice, empresa, resultado, rps_numero=numero_rps)
        # TODO ConsultarUrlNfse: preencher resultado.pdf_path com a URL/PDF oficial.

    return resultado.to_dict()


def _abrasf_endpoint() -> str:
    """Retorna a URL do webservice ABRASF 2.04 conforme o ambiente configurado."""
    if settings.nfse_ambiente == "producao":
        return settings.nfse_ws_url_abrasf_producao
    return settings.nfse_ws_url_abrasf_homologacao


# -----------------------------------------------------------------------------
# Numeração de RPS série 3 (contador próprio, reutiliza o lock do contador da DPS)
# -----------------------------------------------------------------------------
_COUNTER_RPS_FILE = Path(settings.nfse_output_dir) / ".contador_rps.json"


def _proximo_numero_rps() -> str:
    """Incrementa atomicamente o contador de RPS (série 3) e retorna sem zeros à esquerda.

    Reutiliza _adquirir_lock_contador/_liberar_lock_contador (lock ENTRE PROCESSOS,
    o mesmo lockfile da DPS) para evitar números duplicados quando webhook e cron de
    boletos rodam ao mesmo tempo. O número é gravado em arquivo separado
    (.contador_rps.json) para não colidir com a numeração da DPS.

    ⚠️ Numeração de RPS no DF: a faixa é solicitada/consultada no portal do ISSnet
    (menu "Solicitação de Documentos Fiscais"). Este contador local deve operar
    DENTRO da faixa liberada — alinhar o valor inicial com o Bruno antes de produção.
    """
    Path(settings.nfse_output_dir).mkdir(parents=True, exist_ok=True)
    fd = _adquirir_lock_contador()
    try:
        if _COUNTER_RPS_FILE.exists():
            data = json.loads(_COUNTER_RPS_FILE.read_text(encoding="utf-8"))
        else:
            data = {"ultimo_numero": 0}
        data["ultimo_numero"] = int(data.get("ultimo_numero", 0)) + 1
        _COUNTER_RPS_FILE.write_text(json.dumps(data), encoding="utf-8")
        return str(data["ultimo_numero"])
    finally:
        _liberar_lock_contador(fd)


# -----------------------------------------------------------------------------
# Montagem do RPS no schema ABRASF 2.04 (via lxml — sem nfelib, sem IBSCBS)
# -----------------------------------------------------------------------------
def _montar_xml_rps_abrasf(
    empresa: Empresa,
    servico: DadosServico,
    endereco_tomador: dict[str, str],
    invoice: dict[str, Any],
    numero_rps: str,
) -> tuple[str, str]:
    """Monta o XML `GerarNfseEnvio` (RPS série 3) conforme o XSD ABRASF 2.04.

    Estrutura (todos os elementos no namespace ABRASF_SCHEMA_NS, elementFormDefault
    qualified; respeitar a ORDEM exata do XSD):

        GerarNfseEnvio
          └── Rps (tcDeclaracaoPrestacaoServico)
                └── InfDeclaracaoPrestacaoServico  Id="..."
                      ├── Rps (tcInfRps  Id="...")
                      │     ├── IdentificacaoRps (Numero, Serie, Tipo)
                      │     ├── DataEmissao (date)
                      │     └── Status (1=Normal)
                      ├── Competencia (date)
                      ├── Servico (tcDadosServico)
                      │     ├── Valores (ValorServicos, ValorIss?, Aliquota?)
                      │     ├── IssRetido (2=Não retido)
                      │     ├── ItemListaServico (LC 116 — ex.: 01.07)
                      │     ├── CodigoTributacaoMunicipio (ex.: 1071)
                      │     ├── Discriminacao
                      │     ├── CodigoMunicipio (5300108)
                      │     └── ExigibilidadeISS (1=Exigível)
                      ├── Prestador (CpfCnpj/Cnpj + InscricaoMunicipal)
                      ├── TomadorServico (IdentificacaoTomador?, RazaoSocial, Endereco?, Contato?)
                      ├── OptanteSimplesNacional (1=Sim)
                      └── IncentivoFiscal (2=Não)

    Returns:
        (xml_str, rps_id) — rps_id é o Id de InfDeclaracaoPrestacaoServico, usado
        como referência (#Id) da assinatura.
    """
    from lxml import etree

    NS = ABRASF_SCHEMA_NS

    def _el(parent, tag, text=None):
        """Cria subelemento qualificado no namespace ABRASF; define texto se houver."""
        e = etree.SubElement(parent, f"{{{NS}}}{tag}")
        if text is not None:
            e.text = str(text)
        return e

    # Identificadores: alfanuméricos curtos (tsIdTag, máx 255). Usamos prefixos
    # legíveis + número do RPS para rastreabilidade nos arquivos arquivados.
    inf_id = f"rps{numero_rps}"
    rps_inf_id = f"id{numero_rps}"

    cnpj_prestador = _so_digitos(settings.nfse_cnpj_prestador)
    im_prestador = _so_digitos(settings.nfse_inscricao_municipal)
    cnpj_tomador = _so_digitos(empresa.cnpj)

    # Município do prestador (emissor) e do tomador.
    cod_mun_prestador = (settings.nfse_codigo_municipio_emissor or "5300108").strip()
    cod_mun_tomador = _codigo_ibge_por_cidade(
        endereco_tomador.get("cidade", ""), endereco_tomador.get("uf", ""),
        default=cod_mun_prestador,
    )

    hoje = date.today().isoformat()

    # ---------- Raiz GerarNfseEnvio > Rps > InfDeclaracaoPrestacaoServico ----------
    envio = etree.Element(f"{{{NS}}}GerarNfseEnvio", nsmap={None: NS})
    rps_decl = _el(envio, "Rps")  # tcDeclaracaoPrestacaoServico
    inf_decl = _el(rps_decl, "InfDeclaracaoPrestacaoServico")
    inf_decl.set("Id", inf_id)

    # ---------- Rps (tcInfRps) — IdentificacaoRps + DataEmissao + Status ----------
    rps_inf = _el(inf_decl, "Rps")  # tcInfRps
    rps_inf.set("Id", rps_inf_id)
    ident_rps = _el(rps_inf, "IdentificacaoRps")
    _el(ident_rps, "Numero", numero_rps)
    _el(ident_rps, "Serie", (settings.nfse_serie_rps or "3"))
    _el(ident_rps, "Tipo", "1")  # 1 = RPS
    _el(rps_inf, "DataEmissao", hoje)
    _el(rps_inf, "Status", "1")  # 1 = Normal

    # ---------- Competencia ----------
    _el(inf_decl, "Competencia", hoje)

    # ---------- Servico (tcDadosServico) — ORDEM do XSD é obrigatória ----------
    serv = _el(inf_decl, "Servico")
    valores = _el(serv, "Valores")  # tcValoresDeclaracaoServico
    valor_serv = _decimal_2(servico.valor_cents)
    _el(valores, "ValorServicos", valor_serv)
    # ValorIss: para Simples Nacional o ISS é recolhido no DAS; o valor calculado é
    # informativo. ISS = base * (alíquota/100), arredondado a 2 casas.
    valor_iss = f"{(servico.valor_cents / 100) * (servico.aliquota_iss / 100):.2f}"
    _el(valores, "ValorIss", valor_iss)
    # Aliquota (tsAliquota = decimal totalDigits=4, fractionDigits=2): percentual,
    # ex.: 2.00 para 2%. ⚠️ NÃO é fração (0.02) — confirmado pelo XSD (máx 99.99).
    _el(valores, "Aliquota", f"{servico.aliquota_iss:.2f}")
    # IssRetido (tsSimNao): 2 = Não retido (tomador não retém ISS).
    _el(serv, "IssRetido", "2")
    # ItemListaServico (tsItemListaServico): subitem LC 116/2003 no formato "NN.NN".
    _el(serv, "ItemListaServico", _formatar_item_lista_servico(servico.codigo_servico))
    # CodigoCnae (tsCodigoCnae = xsd:int, totalDigits=7): obrigatório no ISSnet DF
    # (erro L001 sem ele). Vem ANTES de CodigoTributacaoMunicipio na ordem do XSD.
    _el(serv, "CodigoCnae", _so_digitos(settings.nfse_cnae))
    # CodigoTributacaoMunicipio (tsCodigoTributacao, string até 20) — ex.: "1071".
    _el(serv, "CodigoTributacaoMunicipio", str(settings.nfse_codigo_trib_municipal))
    # Discriminacao (obrigatório, máx 2000).
    _el(serv, "Discriminacao", servico.descricao[:2000])
    # CodigoMunicipio de prestação (IBGE) — Brasília 5300108.
    _el(serv, "CodigoMunicipio", cod_mun_prestador)
    # ExigibilidadeISS (tsExigibilidadeISS): 1 = Exigível.
    _el(serv, "ExigibilidadeISS", "1")
    # MunicipioIncidencia (tsCodigoMunicipioIbge = xsd:int, totalDigits=7): obrigatório
    # quando ExigibilidadeISS=1 (erro E311 sem ele). Vem DEPOIS de ExigibilidadeISS na
    # ordem do XSD — Brasília 5300108 (mesmo município da prestação).
    _el(serv, "MunicipioIncidencia", _so_digitos(settings.nfse_municipio_incidencia))

    # ---------- Prestador (tcIdentificacaoPessoaEmpresa) ----------
    prest = _el(inf_decl, "Prestador")
    cpfcnpj_prest = _el(prest, "CpfCnpj")
    _el(cpfcnpj_prest, "Cnpj", cnpj_prestador)
    if im_prestador:
        _el(prest, "InscricaoMunicipal", im_prestador[:15])

    # ---------- TomadorServico (tcDadosTomador) ----------
    toma = _el(inf_decl, "TomadorServico")
    if cnpj_tomador:
        ident_toma = _el(toma, "IdentificacaoTomador")
        cpfcnpj_toma = _el(ident_toma, "CpfCnpj")
        # Tomador pode ser CNPJ (14) ou CPF (11).
        if len(cnpj_tomador) == 14:
            _el(cpfcnpj_toma, "Cnpj", cnpj_tomador)
        elif len(cnpj_tomador) == 11:
            _el(cpfcnpj_toma, "Cpf", cnpj_tomador)
        else:
            _el(cpfcnpj_toma, "Cnpj", cnpj_tomador.zfill(14)[:14])
    _el(toma, "RazaoSocial", (empresa.razao_social or "TOMADOR")[:150])
    # Endereco (tcEndereco) — todos os filhos obrigatórios exceto Complemento;
    # só monta se houver dados mínimos (logradouro+cidade+CEP) para não enviar lixo.
    cep = endereco_tomador.get("cep", "")
    if endereco_tomador.get("logradouro") and endereco_tomador.get("cidade") and len(cep) == 8:
        end = _el(toma, "Endereco")
        _el(end, "Endereco", endereco_tomador["logradouro"][:125])
        _el(end, "Numero", (endereco_tomador.get("numero") or "S/N")[:60])
        if endereco_tomador.get("complemento"):
            _el(end, "Complemento", endereco_tomador["complemento"][:60])
        _el(end, "Bairro", (endereco_tomador.get("bairro") or "Centro")[:60])
        _el(end, "CodigoMunicipio", cod_mun_tomador)
        _el(end, "Uf", (endereco_tomador.get("uf") or "DF")[:2].upper())
        _el(end, "Cep", cep)
    # Contato (tcContato): só Email (sequência alternativa do choice) se houver.
    if empresa.email:
        contato = _el(toma, "Contato")
        _el(contato, "Email", empresa.email[:80])

    # ---------- OptanteSimplesNacional + IncentivoFiscal (obrigatórios) ----------
    # 1 = Sim (MEGASUPORTE é optante do Simples Nacional).
    _el(inf_decl, "OptanteSimplesNacional", "1")
    # 2 = Não (sem incentivo fiscal).
    _el(inf_decl, "IncentivoFiscal", "2")

    xml_str = etree.tostring(envio, encoding="unicode", xml_declaration=False)
    return xml_str, inf_id


def _formatar_item_lista_servico(codigo_servico: str) -> str:
    """Converte o código de serviço para o formato tsItemListaServico ('NN.NN').

    O XSD ABRASF 2.04 enumera os subitens da LC 116/2003 no formato "NN.NN"
    (ex.: "01.07"). Aceita entradas como "01.07", "0107", "010701" (cTribNac de 6
    dígitos, do qual usamos os 4 primeiros) e normaliza para "NN.NN".

    Exemplos:
        "01.07"   → "01.07"
        "0107"    → "01.07"
        "010701"  → "01.07"   (cTribNac: item 01, subitem 07, desdobro 01)
        "1.07"    → "01.07"
    """
    digits = _so_digitos(codigo_servico)
    if len(digits) >= 4:
        return f"{digits[:2]}.{digits[2:4]}"
    if len(digits) == 3:
        # "107" → item 01, subitem 07 (assume item de 1 dígito + subitem de 2)
        return f"0{digits[0]}.{digits[1:3]}"
    # Fallback: devolve o original (deixa o XSD/ISSnet rejeitar se inválido)
    return codigo_servico


# -----------------------------------------------------------------------------
# Envelope SOAP ABRASF (nfseCabecMsg + nfseDadosMsg) — operação GerarNfse
# -----------------------------------------------------------------------------
def _envelopar_soap_abrasf(xml_rps_assinado: str) -> str:
    """Embrulha o RPS assinado no envelope SOAP 1.1 do ISSnet/ABRASF 2.04.

    O webservice ABRASF expõe operações com dois parâmetros string (ver WSDL):
        nfseCabecMsg → cabeçalho com a versão do leiaute (2.04)
        nfseDadosMsg → o GerarNfseEnvio assinado

    O conteúdo de nfseDadosMsg é o XML aninhado (padrão ISSnet v2.04). Caso o
    ISSnet DF exija CDATA, alternar para o ramo comentado abaixo após validação.

    Args:
        xml_rps_assinado: GerarNfseEnvio já assinado (Signature embutida no Rps).
    """
    return _envelopar_soap_abrasf_operacao(xml_rps_assinado, ABRASF_OPERACAO)


def _envelopar_soap_abrasf_operacao(xml_dados: str, operacao: str) -> str:
    """Embrulha um XML de dados ABRASF 2.04 no envelope SOAP 1.1 para `operacao`.

    Generaliza `_envelopar_soap_abrasf` (que fica como atalho para GerarNfse): o
    mesmo padrão nfseCabecMsg + nfseDadosMsg vale para todas as operações do WSDL
    ISSnet (GerarNfse, ConsultarUrlNfse, etc.), mudando só o nome do elemento da
    operação no corpo. O conteúdo de nfseDadosMsg é o XML aninhado (sem CDATA);
    se o ISSnet exigir CDATA, trocar nos dois ramos comentados abaixo.

    Args:
        xml_dados: XML de dados ABRASF (ex.: GerarNfseEnvio, ConsultarUrlNfseEnvio).
        operacao: nome da operação SOAP (ex.: "GerarNfse", "ConsultarUrlNfse").
    """
    # Remove declaração XML do corpo interno (não pode aparecer aninhada).
    corpo = re.sub(r'<\?xml[^?]*\?>\s*', '', xml_dados).strip()

    cabecalho = (
        f'<cabecalho versao="{ABRASF_VERSAO_DADOS}" xmlns="{ABRASF_SCHEMA_NS}">'
        f'<versaoDados>{ABRASF_VERSAO_DADOS}</versaoDados>'
        f'</cabecalho>'
    )

    # Padrão ISSnet v2.04: XML aninhado (sem CDATA). Se o ISSnet exigir CDATA,
    # trocar por: cabec = f'<![CDATA[{cabecalho}]]>' ; dados = f'<![CDATA[{corpo}]]>'
    cabec_content = cabecalho
    dados_content = corpo

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\n'
        '  <soap:Body>\n'
        f'    <{operacao} xmlns="{ABRASF_SERVICE_NS}">\n'
        f'      <nfseCabecMsg>{cabec_content}</nfseCabecMsg>\n'
        f'      <nfseDadosMsg>{dados_content}</nfseDadosMsg>\n'
        f'    </{operacao}>\n'
        '  </soap:Body>\n'
        '</soap:Envelope>\n'
    )


# -----------------------------------------------------------------------------
# Pós-processamento da Signature (ABRASF 2.04 exige Signature DENTRO de <Rps>)
# -----------------------------------------------------------------------------
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"


def _reposicionar_signature_dentro_de_rps(xml_assinado: str) -> str:
    """Move a <Signature> do XMLDSig para dentro de <Rps> (XSD ABRASF 2.04).

    `erpbrasil.assinatura` insere a <Signature> como filha do nó RAIZ da árvore
    fornecida (no nosso caso `GerarNfseEnvio`), ficando como IRMÃ de `<Rps>`.
    Mas o tipo `tcDeclaracaoPrestacaoServico` do XSD ABRASF 2.04 exige a
    sequência (InfDeclaracaoPrestacaoServico, dsig:Signature?) — ou seja, a
    Signature precisa ser FILHA de `<Rps>` (irmã de InfDeclaracaoPrestacaoServico).
    Sem este reposicionamento o ISSnet rejeita o RPS com E160.

    A busca é tolerante a namespace (`etree.QName(el).localname`) para funcionar
    independente de como o assinador serializou o XML. Se a Signature já estiver
    no lugar correto (ou ausente), o XML é retornado inalterado.

    Args:
        xml_assinado: GerarNfseEnvio já assinado, como string UTF-8 sem
            declaração XML (formato produzido por `_assinar_xml`).

    Returns:
        XML com a Signature movida para dentro de `<Rps>`, serializado sem
        declaração XML (mesmo formato de saída do `_assinar_xml`).
    """
    from lxml import etree

    # Parser seguro (defesa em profundidade contra XXE — Fix 2.2).
    root = etree.fromstring(xml_assinado.encode("utf-8"), parser=_get_safe_xml_parser())

    # Localiza a <Signature> pelo localname + namespace XMLDSig (tolerante a prefixo).
    signature = None
    for el in root.iter():
        qn = etree.QName(el)
        if qn.localname == "Signature" and qn.namespace == DSIG_NS:
            signature = el
            break
    if signature is None:
        # Nenhuma Signature presente — nada a fazer (caminho não esperado, mas
        # mantemos idempotência: devolvemos o XML como veio).
        return xml_assinado

    # Localiza <InfDeclaracaoPrestacaoServico> (filho de <Rps>) por localname.
    inf_decl = None
    for el in root.iter():
        if etree.QName(el).localname == "InfDeclaracaoPrestacaoServico":
            inf_decl = el
            break
    if inf_decl is None:
        # Estrutura inesperada — devolve o XML como veio em vez de mascarar o erro.
        logger.warning(
            "Signature presente mas InfDeclaracaoPrestacaoServico não encontrado; "
            "reposicionamento da Signature ignorado."
        )
        return xml_assinado

    rps_pai = inf_decl.getparent()  # esperado: o <Rps> (tcDeclaracaoPrestacaoServico)
    if rps_pai is None:
        logger.warning(
            "InfDeclaracaoPrestacaoServico sem pai — reposicionamento da Signature ignorado."
        )
        return xml_assinado

    # Já está no lugar certo? Mantém ordem (Inf... primeiro, Signature depois).
    if signature.getparent() is rps_pai:
        return xml_assinado

    # Move: remove do pai atual e faz append no <Rps>. O append garante que a
    # Signature fique APÓS InfDeclaracaoPrestacaoServico, respeitando a sequência
    # exigida pelo XSD.
    signature.getparent().remove(signature)
    rps_pai.append(signature)

    return etree.tostring(root, encoding="unicode", xml_declaration=False)


def _enviar_soap_abrasf(envelope: str, endpoint: str) -> tuple[str, int]:
    """Envia o envelope SOAP ABRASF ao endpoint do ISSnet via httpx com mTLS.

    Reutiliza _pkcs12_to_pem_tempfiles (mesmo certificado A1 do backend nacional)
    para o handshake mútuo. Remove os arquivos PEM temporários no finally.

    Returns:
        (corpo_da_resposta, status_code_http)
    """
    return _enviar_soap_abrasf_operacao(envelope, endpoint, ABRASF_SOAP_ACTION)


def _enviar_soap_abrasf_operacao(
    envelope: str, endpoint: str, soap_action: str
) -> tuple[str, int]:
    """Envia um envelope SOAP ABRASF ao ISSnet via httpx com mTLS, com SOAPAction custom.

    Generaliza `_enviar_soap_abrasf` (atalho para GerarNfse) — o único parâmetro que
    muda entre operações é o header SOAPAction. Reutiliza `_pkcs12_to_pem_tempfiles`
    (mesmo certificado A1) e remove os PEMs temporários no finally.

    Args:
        envelope: corpo SOAP completo (string UTF-8).
        endpoint: URL do webservice ABRASF.
        soap_action: valor do header SOAPAction (ex.: ABRASF_SOAP_ACTION_CONSULTA_URL).

    Returns:
        (corpo_da_resposta, status_code_http)
    """
    cert_path = Path(settings.nfse_certificado_path)
    senha = settings.nfse_certificado_senha
    cert_file, key_file = _pkcs12_to_pem_tempfiles(cert_path, senha)

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{soap_action}"',
    }
    try:
        logger.info(
            f"Enviando SOAP (action={soap_action}, ABRASF 2.04) para {endpoint}"
        )
        with httpx.Client(
            cert=(str(cert_file), str(key_file)) if cert_file.exists() else None,
            timeout=60.0,
            verify=True,
        ) as client:
            response = client.post(
                endpoint, content=envelope.encode("utf-8"), headers=headers
            )
        return response.text, response.status_code
    finally:
        # Garante remoção dos PEMs temporários do cert/chave A1 — material
        # sensível, não deixar órfão no /tmp. Falhas raras (lock no Windows,
        # arquivo já removido) viram WARNING para auditoria, em vez de
        # silenciar (Fix 2.1 — defesa em profundidade).
        for f in (cert_file, key_file):
            try:
                if f and f.exists():
                    f.unlink()
            except Exception as exc:
                logger.warning(
                    f"Falha ao remover PEM temporário {f}: {exc}"
                )


# -----------------------------------------------------------------------------
# Parsing do retorno GerarNfseResposta (ABRASF 2.04) — tolerante a namespace
# -----------------------------------------------------------------------------
def _parsear_resposta_abrasf(xml_resposta: str) -> dict[str, Any]:
    """Extrai dados de GerarNfseResposta (ABRASF 2.04), buscando por localname.

    Estrutura esperada (XSD ABRASF 2.04):
        GerarNfseResposta
          ├── ListaNfse                      (sucesso)
          │     └── CompNfse > Nfse > InfNfse
          │           ├── Numero             ← número da NFS-e
          │           ├── CodigoVerificacao  ← código de verificação
          │           └── ...
          │     └── ListaMensagemAlertaRetorno?  (avisos não-fatais)
          └── ListaMensagemRetorno           (erros — RPS rejeitado)
                └── MensagemRetorno (Codigo, Mensagem, Correcao?)

    Sucesso = presença de <Numero> dentro de <InfNfse>. O envelope SOAP é
    transparentemente ignorado pela busca por localname.
    """
    from lxml import etree

    try:
        # Fix 2.2 — parser seguro (anti-XXE, sem rede, sem DTD inline). A resposta
        # do webservice ABRASF/ISSnet vem de fonte externa e nunca deve resolver
        # entidades externas nem buscar recursos por URL.
        root = etree.fromstring(xml_resposta.encode("utf-8"), parser=_get_safe_xml_parser())
    except Exception as exc:
        return {
            "sucesso": False,
            "mensagem_erro": f"Retorno não é XML válido: {exc}",
            "mensagens": [],
        }

    def _iter_local(tag: str):
        for el in root.iter():
            if etree.QName(el).localname == tag:
                yield el

    # 1. Número + código de verificação: buscar dentro de InfNfse (evita pegar o
    #    Numero do IdentificacaoRps, que também se chama "Numero").
    numero_nfse = None
    codigo_verif = None
    for inf in _iter_local("InfNfse"):
        for child in inf.iter():
            ln = etree.QName(child).localname
            if ln == "Numero" and numero_nfse is None and child.text:
                numero_nfse = child.text.strip()
            elif ln == "CodigoVerificacao" and codigo_verif is None and child.text:
                codigo_verif = child.text.strip()
        if numero_nfse:
            break

    # 2. Mensagens de rejeição (ListaMensagemRetorno) e alerta (ListaMensagemAlertaRetorno)
    mensagens_erro: list[str] = []
    mensagens_alerta: list[str] = []

    for lista in _iter_local("ListaMensagemRetorno"):
        for msg in lista.iter():
            if etree.QName(msg).localname == "MensagemRetorno":
                codigo = _text_child(msg, "Codigo")
                texto = _text_child(msg, "Mensagem")
                correcao = _text_child(msg, "Correcao")
                if texto:
                    full = f"[{codigo or '?'}] {texto}"
                    if correcao:
                        full += f" — Correção: {correcao}"
                    mensagens_erro.append(full)

    for lista in _iter_local("ListaMensagemAlertaRetorno"):
        for msg in lista.iter():
            if etree.QName(msg).localname == "MensagemRetorno":
                codigo = _text_child(msg, "Codigo")
                texto = _text_child(msg, "Mensagem")
                if texto:
                    mensagens_alerta.append(f"[{codigo or '?'}] {texto}")

    sucesso = bool(numero_nfse)
    todas_mensagens = mensagens_erro + mensagens_alerta

    mensagem_erro = None
    if not sucesso:
        if mensagens_erro:
            mensagem_erro = mensagens_erro[0]
        elif mensagens_alerta:
            mensagem_erro = mensagens_alerta[0]
        else:
            fault = None
            for tag in ("faultstring", "Reason", "Text"):
                fault = fault or next(
                    (e.text.strip() for e in _iter_local(tag) if e.text), None
                )
            mensagem_erro = fault or "NFS-e (ABRASF) não aprovada (sem mensagem explícita)"

    return {
        "sucesso": sucesso,
        "numero_nfse": numero_nfse,
        "codigo_verificacao": codigo_verif,
        "mensagens": todas_mensagens,
        "mensagens_erro": mensagens_erro,
        "mensagens_alerta": mensagens_alerta,
        "mensagem_erro": mensagem_erro,
    }


# =============================================================================
# ConsultarUrlNfse — operação EXCLUSIVA do ISSnet DF (ABRASF 2.04)
# =============================================================================
# Obtém a URL oficial de visualização/impressão de uma NFS-e já emitida (e, a
# partir dela, o PDF/DANFSE oficial). Esta operação NÃO existe no XSD/WSDL
# genérico do ABRASF (docs/exemplos_oficiais/abrasf204/) — só no ISSnet DF
# (df.issnetonline.com.br/webservicenfse204/nfse.asmx?wsdl). A operação SOAP é
# "ConsultarUrlNfse" (confirmada respondendo), mas o CONTEÚDO do nfseDadosMsg usa
# o schema PRÓPRIO do ISSnet `ConsultarUrlVisualizacaoNfseEnvio` (XSDs oficiais,
# elementFormDefault=qualified) — não o leiaute genérico ABRASF, que dava E160.
# Pontos ainda passíveis de ajuste pós-teste:
#   - se a consulta exige ou não assinatura XML (não exige hoje);
#   - o nome do elemento de URL na resposta (UrlVisualizacaoNfse / Url) — a
#     heurística do parser tolera localname com "url" ou texto começando em http.
# As constantes (namespaces + elemento raiz) ficam no topo da seção.
# =============================================================================

# Operação ISSnet DF para obter a URL oficial da NFS-e.
# IMPORTANTE: a OPERAÇÃO SOAP continua "ConsultarUrlNfse" (já confirmamos que esse
# endpoint/SOAPAction responde). O que muda é só o SCHEMA INTERNO do nfseDadosMsg:
# o conteúdo NÃO é o leiaute genérico ABRASF (que dava E160), e sim o schema PRÓPRIO
# do ISSnet `ConsultarUrlVisualizacaoNfseEnvio`.
ABRASF_OPERACAO_CONSULTA_URL = "ConsultarUrlNfse"
# SOAPAction da operação (mesmo binding document/literal das demais operações).
ABRASF_SOAP_ACTION_CONSULTA_URL = f"{ABRASF_SERVICE_NS}/{ABRASF_OPERACAO_CONSULTA_URL}"

# Namespaces PRÓPRIOS do ISSnet para a consulta de URL de visualização (XSDs
# oficiais, elementFormDefault=qualified). NÃO confundir com ABRASF_SCHEMA_NS.
#   - Namespace do ENVIO: raiz + filhos diretos (Prestador, Numero, Codigo...).
#   - Namespace tipos_complexos: CpfCnpj / Cnpj / InscricaoMunicipal (prefixo tc).
ISSNET_CONSULTA_URL_ENVIO_NS = (
    "http://www.issnetonline.com.br/webserviceabrasf/vsd/"
    "servico_consultar_url_visualizacao_nfse_envio.xsd"
)
ISSNET_TIPOS_COMPLEXOS_NS = (
    "http://www.issnetonline.com.br/webserviceabrasf/vsd/tipos_complexos.xsd"
)
# Nome do elemento raiz do nfseDadosMsg (schema próprio do ISSnet).
ISSNET_CONSULTA_URL_ELEMENTO_RAIZ = "ConsultarUrlVisualizacaoNfseEnvio"


def _montar_xml_consultar_url_nfse(numero_nfse: str) -> str:
    """Monta o XML `ConsultarUrlVisualizacaoNfseEnvio` (schema PRÓPRIO do ISSnet DF).

    Este é o leiaute que vai DENTRO do nfseDadosMsg. A versão anterior usava o
    namespace/elemento genérico do ABRASF (`ConsultarUrlNfseEnvio` no NS
    `http://www.abrasf.org.br/nfse.xsd`), e o ISSnet rejeitava com E160. O schema
    correto é o próprio do ISSnet, confirmado nos XSDs oficiais (elementFormDefault
    = qualified), com DOIS namespaces distintos:

        ConsultarUrlVisualizacaoNfseEnvio            (NS de ENVIO — xmlns default)
          ├── Prestador                              (NS de ENVIO)
          │     ├── tc:CpfCnpj > tc:Cnpj             (NS tipos_complexos)  ← CNPJ prestador
          │     └── tc:InscricaoMunicipal           (NS tipos_complexos)  ← IM prestador
          ├── Numero                                 (NS de ENVIO)         ← número da NFS-e (ex.: "408")
          └── CodigoTributacaoMunicipio              (NS de ENVIO)         ← settings.nfse_codigo_trib_municipal (=1071)

    Regras de namespace (críticas — foi o que causou o E160):
      - Raiz + Prestador + Numero + CodigoTributacaoMunicipio → NS de ENVIO
        (ISSNET_CONSULTA_URL_ENVIO_NS), declarado como xmlns default.
      - CpfCnpj / Cnpj / InscricaoMunicipal → NS tipos_complexos
        (ISSNET_TIPOS_COMPLEXOS_NS), declarado com prefixo `tc`.

    NÃO assinamos: esta consulta dispensa XMLDSig.

    Args:
        numero_nfse: número da NFS-e a consultar (ex.: "408").

    Returns:
        XML como string UTF-8 sem declaração XML (pronto para envelopar).
    """
    from lxml import etree

    ENVIO = ISSNET_CONSULTA_URL_ENVIO_NS
    TC = ISSNET_TIPOS_COMPLEXOS_NS

    def _el_envio(parent, tag, text=None):
        # Elemento no namespace de ENVIO (xmlns default da raiz).
        e = etree.SubElement(parent, f"{{{ENVIO}}}{tag}")
        if text is not None:
            e.text = str(text)
        return e

    def _el_tc(parent, tag, text=None):
        # Elemento no namespace tipos_complexos (prefixo tc).
        e = etree.SubElement(parent, f"{{{TC}}}{tag}")
        if text is not None:
            e.text = str(text)
        return e

    cnpj_prestador = _so_digitos(settings.nfse_cnpj_prestador)
    im_prestador = _so_digitos(settings.nfse_inscricao_municipal)
    cod_trib_mun = str(settings.nfse_codigo_trib_municipal).strip()

    # nsmap: default = NS de envio; tc = tipos_complexos. Declarar ambos na raiz
    # garante que o lxml serialize CpfCnpj/Cnpj/InscricaoMunicipal com prefixo tc:.
    envio = etree.Element(
        f"{{{ENVIO}}}{ISSNET_CONSULTA_URL_ELEMENTO_RAIZ}",
        nsmap={None: ENVIO, "tc": TC},
    )

    # Prestador (NS de envio) com identificação no NS tipos_complexos (tc:).
    prest = _el_envio(envio, "Prestador")
    cpfcnpj = _el_tc(prest, "CpfCnpj")
    _el_tc(cpfcnpj, "Cnpj", cnpj_prestador)
    if im_prestador:
        _el_tc(prest, "InscricaoMunicipal", im_prestador)

    # Número da NFS-e e código de tributação municipal (ambos no NS de envio).
    _el_envio(envio, "Numero", str(numero_nfse).strip())
    _el_envio(envio, "CodigoTributacaoMunicipio", cod_trib_mun)

    return etree.tostring(envio, encoding="unicode", xml_declaration=False)


async def consultar_url_nfse(numero_nfse: str) -> dict[str, Any]:
    """Consulta a URL oficial de uma NFS-e no ISSnet DF (operação ConsultarUrlNfse).

    Fluxo (reaproveita toda a infra SOAP/mTLS do backend ABRASF):
        1. Monta ConsultarUrlNfseEnvio (sem assinatura — ver _montar_xml_...)
        2. Envelopa em SOAP (nfseCabecMsg + nfseDadosMsg), operação ConsultarUrlNfse
        3. Envia via httpx com mTLS ao endpoint ABRASF do ambiente atual
        4. Arquiva envio + retorno em nfse_emitidas/
        5. Parseia a resposta procurando o elemento de URL (tolerante a namespace)

    NÃO faz parte do fluxo de emissão (não é chamada por _emitir_abrasf204 ainda) —
    é um utilitário para obter a URL/PDF de uma NFS-e já emitida.

    Args:
        numero_nfse: número da NFS-e (ex.: "408").

    Returns:
        dict: {"sucesso", "url", "mensagens", "raw_response" (truncado p/ log)}.
    """
    resultado: dict[str, Any] = {
        "sucesso": False,
        "url": None,
        "mensagens": [],
        "raw_response": "",
    }

    # 1. Montar o XML de consulta (sem assinatura — ABRASF dispensa em consultas).
    try:
        xml_consulta = _montar_xml_consultar_url_nfse(numero_nfse)
    except Exception as exc:
        logger.exception("Falha ao montar ConsultarUrlNfseEnvio")
        resultado["mensagens"] = [f"Erro ao montar consulta: {exc}"]
        return resultado

    # 2. Envelopar em SOAP (mesmo padrão do GerarNfse), porém com a operação e a
    #    SOAPAction de ConsultarUrlNfse.
    envelope = _envelopar_soap_abrasf_operacao(
        xml_consulta, ABRASF_OPERACAO_CONSULTA_URL
    )

    # Arquiva o envio (mesmo em caso de erro adiante — diagnóstico).
    try:
        _arquivar(envelope, prefix=f"nfse_{numero_nfse}", suffix="consulta_url_enviado")
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar envio da consulta de URL: {exc}")

    # 3. Enviar (mTLS) ao endpoint do ambiente atual.
    endpoint = _abrasf_endpoint()
    if not endpoint:
        resultado["mensagens"] = [
            "URL do webservice ABRASF 2.04 não configurada "
            "(NFSE_WS_URL_ABRASF_PRODUCAO / _HOMOLOGACAO no .env)."
        ]
        return resultado

    try:
        resposta_xml, status_code = _enviar_soap_abrasf_operacao(
            envelope, endpoint, ABRASF_SOAP_ACTION_CONSULTA_URL
        )
    except Exception as exc:
        logger.exception("Falha ao enviar ConsultarUrlNfse ao webservice")
        resultado["mensagens"] = [f"Erro ao enviar ao webservice: {exc}"]
        return resultado

    # 4. Arquivar o retorno.
    try:
        _arquivar(
            resposta_xml, prefix=f"nfse_{numero_nfse}", suffix="consulta_url_retorno"
        )
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar retorno da consulta de URL: {exc}")

    # raw_response truncado (8 KB) — o suficiente para inspecionar a estrutura sem
    # poluir o log com um corpo enorme.
    resultado["raw_response"] = resposta_xml[:8000]

    if status_code >= 400:
        resultado["mensagens"] = [
            f"Webservice ABRASF retornou HTTP {status_code} na ConsultarUrlNfse."
        ]
        logger.error(
            f"[ConsultarUrlNfse] HTTP {status_code} para {endpoint} "
            f"(NFS-e {numero_nfse})"
        )
        return resultado

    # 5. Parsear a resposta atrás da URL + eventuais mensagens de erro.
    try:
        info = _parsear_resposta_consultar_url(resposta_xml)
        resultado["url"] = info.get("url")
        resultado["mensagens"] = info.get("mensagens", [])
        resultado["sucesso"] = bool(info.get("url"))
    except Exception as exc:
        logger.exception("Falha ao parsear retorno da ConsultarUrlNfse")
        resultado["mensagens"] = [f"Erro ao parsear retorno: {exc}"]

    return resultado


def _parsear_resposta_consultar_url(xml_resposta: str) -> dict[str, Any]:
    """Extrai a URL de ConsultarUrlNfseResposta (tolerante a namespace).

    Procura, por localname, qualquer elemento cujo nome CONTENHA "Url" (ex.:
    UrlNfse, Url, UrlVisualizacaoNfse) ou cujo TEXTO comece com "http". Também
    coleta ListaMensagemRetorno (erros) se houver. Tudo por localname para ignorar
    o envelope SOAP e variações de prefixo/namespace do ISSnet.

    ⚠️ AJUSTÁVEL PÓS-TESTE: se o ISSnet aninhar a URL num elemento com nome
    inesperado (sem "Url" e sem texto http), inspecionar o raw_response e ajustar
    a heurística abaixo.
    """
    from lxml import etree

    root = etree.fromstring(
        xml_resposta.encode("utf-8"), parser=_get_safe_xml_parser()
    )

    url: Optional[str] = None
    for el in root.iter():
        if el.text is None:
            continue
        texto = el.text.strip()
        if not texto:
            continue
        localname = etree.QName(el).localname
        # 1ª heurística: nome do elemento contém "Url". 2ª: o texto é uma URL http.
        if "url" in localname.lower() or texto.lower().startswith("http"):
            url = texto
            break

    # Mensagens de rejeição (mesmo formato do GerarNfse).
    mensagens: list[str] = []
    for el in root.iter():
        if etree.QName(el).localname == "MensagemRetorno":
            codigo = _text_child(el, "Codigo")
            texto = _text_child(el, "Mensagem")
            correcao = _text_child(el, "Correcao")
            if texto:
                full = f"[{codigo or '?'}] {texto}"
                if correcao:
                    full += f" — Correção: {correcao}"
                mensagens.append(full)

    # Se não achou URL nem mensagem, tenta um SOAP Fault para não mascarar erro.
    if not url and not mensagens:
        for tag in ("faultstring", "Reason", "Text"):
            fault = next(
                (e.text.strip() for e in root.iter()
                 if etree.QName(e).localname == tag and e.text and e.text.strip()),
                None,
            )
            if fault:
                mensagens.append(fault)
                break

    return {"url": url, "mensagens": mensagens}


def baixar_pdf_nfse(url: str) -> Optional[bytes]:
    """Baixa o PDF/DANFSE oficial a partir da URL retornada por ConsultarUrlNfse.

    Faz um GET (httpx, follow_redirects, timeout 30s) SEM certificado — a página de
    visualização do ISSnet costuma ser pública. Se o servidor exigir mTLS (raro),
    ajustar para reutilizar `_pkcs12_to_pem_tempfiles` (ver comentário abaixo).

    Considera PDF quando o Content-Type indica PDF OU o conteúdo começa com o magic
    number "%PDF". Caso contrário (HTML de visualização) retorna None — cabe ao
    chamador decidir o que fazer com a página.

    Args:
        url: URL retornada por consultar_url_nfse().

    Returns:
        bytes do PDF se for um PDF direto; None se for HTML/outro conteúdo.
    """
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, verify=True) as client:
            resp = client.get(url)
    except Exception as exc:
        logger.warning(f"[baixar_pdf_nfse] Falha no GET de {url}: {exc}")
        return None

    content_type = resp.headers.get("content-type", "")
    conteudo = resp.content
    logger.info(
        f"[baixar_pdf_nfse] GET {url} → HTTP {resp.status_code}, "
        f"content-type='{content_type}', {len(conteudo)} bytes"
    )

    if resp.status_code >= 400:
        return None

    eh_pdf = "pdf" in content_type.lower() or conteudo[:5] == b"%PDF-"
    if eh_pdf:
        return conteudo

    # Não é PDF direto (provavelmente uma página HTML de visualização).
    return None


# =============================================================================
# BACKEND NACIONAL — Padrão Nacional CGNFS-e (DPS v1.01)
# =============================================================================
# Este é exatamente o código que `emitir_nfse` executava antes do ADR-0005;
# apenas movido para um backend nomeado, SEM qualquer mudança de lógica. Continua
# sendo o caminho de produção atual (settings.nfse_padrao="nacional", default).
# =============================================================================
async def _emitir_nacional(invoice: dict[str, Any], empresa: Empresa) -> dict[str, Any]:
    """Emite uma NFS-e no DF (Padrão Nacional / DPS) para uma fatura paga da Iugu.

    Args:
        invoice: payload completo da fatura da Iugu (tem payer_address_*, payer_name, etc.)
        empresa: dados cadastrais da empresa tomadora (planilha)

    Returns:
        Resultado em dict (ResultadoEmissao.to_dict()).
    """
    if not NFELIB_DISPONIVEL or not ERPBRASIL_DISPONIVEL:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro=(
                "Dependências ausentes. Rode: "
                "pip install nfelib erpbrasil.assinatura"
            ),
        ).to_dict()

    # 1. Validação básica
    total_cents = int(
        invoice.get("total_paid_cents") or invoice.get("total_cents") or 0
    )
    if total_cents <= 0:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro="Valor da fatura inválido ou zero",
        ).to_dict()

    problemas = _validar_configuracao(empresa)
    if problemas:
        return ResultadoEmissao(
            sucesso=False,
            mensagem_erro="Configuração incompleta: " + "; ".join(problemas),
        ).to_dict()

    # 2. Montar dados do serviço (prioridade: planilha > resume itens > default .env)
    servico = DadosServico(
        codigo_servico=empresa.codigo_servico or settings.nfse_codigo_servico_padrao,
        descricao=empresa.descricao_servico or _resumir_itens(invoice) or settings.nfse_descricao_servico_padrao,
        valor_cents=total_cents,
        aliquota_iss=empresa.aliquota_iss or settings.nfse_aliquota_iss_padrao,
    )

    endereco_tomador = extrair_endereco_tomador(invoice)

    logger.info(
        f"[NFS-e] Iniciando emissão: tomador={empresa.razao_social} "
        f"valor={format_cents_to_br(total_cents)} "
        f"serviço={servico.codigo_servico} "
        f"ambiente={settings.nfse_ambiente}"
        f"{' [DRY-RUN]' if settings.nfse_dry_run else ''}"
    )

    # --- DRY-RUN: monta os dados e loga, mas não gera XML nem envia ---
    if settings.nfse_dry_run:
        logger.info(
            f"[NFS-e DRY-RUN] Dados montados com sucesso:\n"
            f"  Tomador:    {empresa.razao_social} ({empresa.cnpj})\n"
            f"  E-mail:     {empresa.email}\n"
            f"  Serviço:    {servico.codigo_servico} — {servico.descricao}\n"
            f"  Valor:      R$ {format_cents_to_br(total_cents)}\n"
            f"  Alíquota:   {servico.aliquota_iss}%\n"
            f"  Endereço:   {endereco_tomador}\n"
            f"  Prestador:  {settings.nfse_razao_social_prestador} ({settings.nfse_cnpj_prestador})\n"
            f"  IM:         {settings.nfse_inscricao_municipal}\n"
            f"  Ambiente:   {settings.nfse_ambiente}\n"
            f"  ⚠️  XML NÃO gerado, NÃO enviado (dry-run ativo)"
        )
        return ResultadoEmissao(
            sucesso=True,
            numero_nfse=f"DRY-RUN-{empresa.cnpj[-4:]}",
            ambiente=f"{settings.nfse_ambiente} (dry-run)",
            mensagens=["DRY-RUN: dados montados com sucesso, XML não gerado"],
        ).to_dict()

    resultado = ResultadoEmissao(
        sucesso=False, ambiente=settings.nfse_ambiente
    )

    # 3. Montar DPS
    try:
        numero_dps = _proximo_numero_dps()
        xml_dps, dps_id = _montar_xml_dps(
            empresa=empresa,
            servico=servico,
            endereco_tomador=endereco_tomador,
            invoice=invoice,
            numero_dps=numero_dps,
        )
    except Exception as exc:
        logger.exception("Falha ao montar DPS")
        resultado.mensagem_erro = f"Erro ao montar DPS: {exc}"
        return resultado.to_dict()

    # 4. Assinar
    try:
        xml_assinado = _assinar_xml(xml_dps, dps_id)
    except Exception as exc:
        logger.exception("Falha ao assinar DPS")
        resultado.mensagem_erro = f"Erro ao assinar XML: {exc}"
        return resultado.to_dict()

    # 5. Arquivar DPS assinada (conteúdo interno, sem envelope SOAP)
    try:
        _arquivar(xml_assinado, prefix=f"dps_{numero_dps}", suffix="dps_assinada")
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar DPS assinada: {exc}")

    # 6. Enviar ao webservice
    endpoint = get_nfse_endpoint()
    if not endpoint:
        resultado.mensagem_erro = (
            "URL do webservice NFS-e DF não configurada. "
            "Preencha NFSE_WS_URL_HOMOLOGACAO ou NFSE_WS_URL_PRODUCAO no .env."
        )
        return resultado.to_dict()

    try:
        resposta_xml, status_code, envelope_enviado = _enviar_ao_webservice(
            xml_assinado, endpoint
        )
    except Exception as exc:
        logger.exception("Falha ao enviar DPS ao webservice")
        resultado.mensagem_erro = f"Erro ao enviar ao webservice: {exc}"
        return resultado.to_dict()

    # 7. Arquivar envelope efetivamente enviado (SOAP completo) + retorno
    try:
        resultado.xml_enviado_path = _arquivar(
            envelope_enviado, prefix=f"dps_{numero_dps}", suffix="enviada"
        )
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar envelope enviado: {exc}")

    try:
        resultado.xml_retorno_path = _arquivar(
            resposta_xml, prefix=f"dps_{numero_dps}", suffix="retorno"
        )
    except Exception as exc:
        logger.warning(f"Não foi possível arquivar retorno: {exc}")

    if status_code >= 400:
        resultado.mensagem_erro = (
            f"Webservice retornou HTTP {status_code}. Veja {resultado.xml_retorno_path}"
        )
        logger.error(
            f"[NFS-e] Webservice respondeu HTTP {status_code} "
            f"para {endpoint} — retorno arquivado em {resultado.xml_retorno_path}"
        )
        return resultado.to_dict()

    # 8. Parsear retorno
    try:
        info = _parsear_resposta(resposta_xml)
        resultado.numero_nfse = info.get("numero_nfse")
        resultado.codigo_verificacao = info.get("codigo_verificacao")
        resultado.mensagens = info.get("mensagens", [])
        resultado.sucesso = info.get("sucesso", False)
        if not resultado.sucesso and not resultado.mensagem_erro:
            resultado.mensagem_erro = (
                info.get("mensagem_erro") or "NFS-e não foi aprovada pelo ISS DF"
            )
    except Exception as exc:
        logger.exception("Falha ao parsear retorno")
        resultado.mensagem_erro = f"Erro ao parsear retorno: {exc}"
        return resultado.to_dict()

    if resultado.sucesso:
        logger.info(
            f"[NFS-e] Emissão OK: número {resultado.numero_nfse} "
            f"código {resultado.codigo_verificacao}"
        )
        # Grava o índice nfse_<invoice_id>.json que a API usa para detectar a nota
        # (listagem/detalhe/dashboard). Resiliente: não derruba a emissão se falhar.
        # No backend nacional o documento de origem é a DPS — passamos numero_dps.
        _gravar_log_nfse(invoice, empresa, resultado, rps_numero=numero_dps)
        # Não geramos mais PDF próprio (reportlab removido). O DANFSE oficial será
        # obtido do ISSnet via ConsultarUrlNfse no futuro; até lá, resultado.pdf_path
        # permanece None e o e-mail segue só com o XML.
        # TODO ConsultarUrlNfse: preencher resultado.pdf_path com a URL/PDF oficial.

    return resultado.to_dict()


# =============================================================================
# Helpers de validação e formatação
# =============================================================================
def _validar_configuracao(empresa: Empresa) -> list[str]:
    """Verifica se todas as configurações necessárias estão presentes."""
    problemas = []
    if not settings.nfse_inscricao_municipal:
        problemas.append("NFSE_INSCRICAO_MUNICIPAL ausente no .env")
    if not settings.nfse_cnpj_prestador:
        problemas.append("NFSE_CNPJ_PRESTADOR ausente no .env")
    if not settings.nfse_razao_social_prestador:
        problemas.append("NFSE_RAZAO_SOCIAL_PRESTADOR ausente no .env")
    if not Path(settings.nfse_certificado_path).exists():
        problemas.append(
            f"Certificado digital não encontrado em {settings.nfse_certificado_path}"
        )
    if not settings.nfse_certificado_senha:
        problemas.append("NFSE_CERTIFICADO_SENHA ausente no .env")
    if not (empresa.codigo_servico or settings.nfse_codigo_servico_padrao):
        problemas.append("Código de serviço não definido (na planilha ou no .env)")
    if not empresa.razao_social:
        problemas.append(f"Empresa {empresa.cnpj} sem razão social na planilha")
    return problemas


def _resumir_itens(invoice: dict[str, Any]) -> str:
    items = invoice.get("items") or []
    descs = [str(it.get("description", "")).strip() for it in items if it.get("description")]
    return " | ".join(descs) if descs else "Prestação de serviços"


def extrair_endereco_tomador(invoice: dict[str, Any]) -> dict[str, str]:
    """Extrai endereço do tomador.

    1) Tenta os campos flat da fatura Iugu (payer_address_*).
    2) Se vierem vazios, busca o customer pelo CNPJ do pagador via API Iugu
       (faturas criadas com só cpf_cnpj+name+email não carregam endereço;
       a Iugu não faz autocomplete automático).

    Se nem a fatura nem o customer têm endereço, retorna dict com campos vazios
    (o XML montado vai falhar na validação do webservice — comportamento
    intencional para sinalizar cadastro incompleto).
    """
    def _limpar(v) -> str:
        return str(v).strip() if v is not None else ""

    def _so_digitos(v) -> str:
        return "".join(filter(str.isdigit, str(v))) if v is not None else ""

    endereco = {
        "logradouro": _limpar(invoice.get("payer_address_street")),
        "numero": _limpar(invoice.get("payer_address_number")),
        "complemento": _limpar(invoice.get("payer_address_complement")),
        "bairro": _limpar(invoice.get("payer_address_district")),
        "cidade": _limpar(invoice.get("payer_address_city")),
        "uf": _limpar(invoice.get("payer_address_state"))[:2].upper(),
        "cep": _so_digitos(invoice.get("payer_address_zip_code")),
    }

    campos_obrigatorios = ("logradouro", "numero", "bairro", "cidade", "uf", "cep")
    if all(endereco[k] for k in campos_obrigatorios):
        return endereco

    cnpj = "".join(filter(str.isdigit, str(invoice.get("payer_cpf_cnpj") or "")))
    if not cnpj:
        logger.warning(
            "Endereço do tomador vazio na fatura e sem CPF/CNPJ para fallback"
        )
        return endereco

    try:
        from .iugu_client import IuguClient

        with IuguClient() as client:
            resp = client.list_customers(query=cnpj, limit=20)
    except Exception as exc:
        logger.warning(f"Falha ao buscar customer Iugu para CNPJ {cnpj}: {exc}")
        return endereco

    candidatos = [
        c for c in (resp.get("items") or [])
        if "".join(filter(str.isdigit, str(c.get("cpf_cnpj") or ""))) == cnpj
    ]
    if not candidatos:
        logger.warning(f"Nenhum customer Iugu encontrado para CNPJ {cnpj}")
        return endereco

    def _completo(c: dict) -> bool:
        return all(c.get(k) for k in ("zip_code", "street", "number", "district", "city", "state"))

    melhor = next((c for c in candidatos if _completo(c)), None)
    if melhor is None:
        logger.warning(
            f"Customer(s) Iugu do CNPJ {cnpj} sem endereço completo — "
            f"cadastro incompleto: {[c.get('id') for c in candidatos]}"
        )
        return endereco

    logger.info(
        f"Endereço do tomador completado via customer Iugu {melhor.get('id')} "
        f"(CNPJ {cnpj})"
    )
    return {
        "logradouro": _limpar(melhor.get("street")),
        "numero": _limpar(melhor.get("number")),
        "complemento": _limpar(melhor.get("complement")),
        "bairro": _limpar(melhor.get("district")),
        "cidade": _limpar(melhor.get("city")),
        "uf": _limpar(melhor.get("state"))[:2].upper(),
        "cep": _so_digitos(melhor.get("zip_code")),
    }


def _so_digitos(valor) -> str:
    return "".join(filter(str.isdigit, str(valor))) if valor is not None else ""


def _decimal_2(cents: int) -> str:
    """Converte centavos (int) para string '1234.56' (formato fiscal)."""
    return f"{cents / 100:.2f}"


def _normalizar_cTribNac(codigo_servico: str) -> str:
    """Converte código de serviço LC116 para o formato cTribNac (6 dígitos numéricos).

    O XSD exige exatamente 6 dígitos numéricos [0-9]{6}:
        - 2 dígitos para Item LC 116
        - 2 dígitos para Subitem LC 116
        - 2 dígitos para Desdobro Nacional

    Exemplos:
        "01.07"  → "010700"
        "1.03"   → "010300"
        "17.11"  → "171100"
        "010700" → "010700"  (já no formato correto)
    """
    digits = _so_digitos(codigo_servico)
    if len(digits) >= 6:
        return digits[:6]
    # Se veio como "0107" (4 dígitos, sem desdobro), adiciona "00" no final
    if len(digits) == 4:
        return digits + "00"
    # Se veio como "103" (3 dígitos), assume item 01 + subitem 03 + desdobro 00
    if len(digits) == 3:
        return f"0{digits[0]}0{digits[1]}{digits[2]}0"
    # Fallback: preenche com zeros à direita até 6
    return digits.ljust(6, "0")[:6]


# =============================================================================
# Numeração de DPS (persistente em arquivo)
# =============================================================================
_COUNTER_FILE = Path(settings.nfse_output_dir) / ".contador_dps.json"
_COUNTER_LOCK_FILE = Path(settings.nfse_output_dir) / ".contador_dps.lock"

# Idade (em segundos) a partir da qual o lockfile é considerado órfão (stale).
# A seção crítica real é escrever ~50 bytes em disco (milissegundos), então 30s
# é MUITO acima do tempo legítimo: só removemos o lock de outro processo se ele
# estiver realmente travado/morto, nunca de um processo vivo porém lento.
_LOCK_STALE_SEG = 30.0


def _adquirir_lock_contador(timeout: float = 10.0) -> int:
    """Adquire um lock ENTRE PROCESSOS para o contador da DPS.

    O webhook (uvicorn) e o cron de boletos são processos separados; sem lock,
    o read-modify-write do contador pode gerar números de DPS duplicados.

    Usa um lockfile via os.open(..., O_CREAT | O_EXCL): a criação exclusiva é
    atômica tanto no Windows quanto no Linux, sem depender de bibliotecas
    externas (fcntl/msvcrt não são portáteis). Faz retry com backoff curto.

    Recuperação de stale lock por IDADE do arquivo: enquanto o lockfile for mais
    novo que `_LOCK_STALE_SEG`, apenas fazemos backoff (assumimos que o dono está
    vivo). Só removemos o lockfile se ele estiver órfão há mais que esse limiar —
    assim não apagamos o lock de um processo vivo porém lento (o que reabriria a
    corrida de numeração). Se o timeout total se esgotar sem aquisição, levantamos
    exceção em vez de seguir sem lock.

    Returns:
        O file descriptor do lockfile (deve ser liberado por _liberar_lock_contador).

    Raises:
        TimeoutError: se não conseguir adquirir o lock dentro do timeout total.
    """
    Path(settings.nfse_output_dir).mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    espera = 0.02
    while True:
        try:
            fd = os.open(str(_COUNTER_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            # Grava identidade + timestamp para diagnóstico e para a checagem de idade.
            os.write(fd, f"{os.getpid()}\n{time.time()}".encode("utf-8"))
            return fd
        except FileExistsError:
            # Lockfile existe: só recupera se estiver órfão há mais que _LOCK_STALE_SEG.
            try:
                idade = time.time() - os.path.getmtime(str(_COUNTER_LOCK_FILE))
            except FileNotFoundError:
                # Foi liberado entre o open e o getmtime — tenta criar de novo já.
                continue
            if idade > _LOCK_STALE_SEG:
                logger.warning(
                    f"Lock do contador DPS órfão há {idade:.0f}s "
                    f"(>{_LOCK_STALE_SEG:.0f}s) — removendo lockfile stale"
                )
                try:
                    _COUNTER_LOCK_FILE.unlink()
                except FileNotFoundError:
                    pass
                continue  # tenta recriar imediatamente
            if time.monotonic() >= deadline:
                # Lock ainda fresco (dono provavelmente vivo) e estouramos o timeout:
                # falhar é mais seguro que seguir sem lock e duplicar numeração.
                raise TimeoutError(
                    f"Não foi possível adquirir o lock do contador DPS em {timeout}s "
                    f"(lockfile vivo há {idade:.1f}s)"
                )
            time.sleep(espera)
            espera = min(espera * 2, 0.25)  # backoff exponencial limitado


def _liberar_lock_contador(fd: int) -> None:
    """Libera o lock do contador: fecha o fd e remove o lockfile.

    Só chegamos aqui se NÓS criamos o lock, então o unlink é nosso. Ainda assim
    protegemos contra FileNotFoundError: um stale-recovery legítimo de outro
    processo pode ter removido o arquivo, e não queremos estourar no finally.
    """
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _COUNTER_LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _proximo_numero_dps() -> str:
    """Incrementa atomicamente o contador da DPS.

    Retorna o número SEM zeros à esquerda (ex: "16", não "000000000000016").
    O XSD v1.01 (TSNumDPS) exige pattern [1-9]{1}[0-9]{0,14} — sem leading zeros.
    O zero-padding para o Id da DPS (45 chars) é feito em _montar_xml_dps().

    O read-modify-write é protegido por um lock entre processos (lockfile) para
    evitar número duplicado quando webhook e cron de boletos rodam ao mesmo tempo.
    """
    Path(settings.nfse_output_dir).mkdir(parents=True, exist_ok=True)
    fd = _adquirir_lock_contador()
    try:
        if _COUNTER_FILE.exists():
            data = json.loads(_COUNTER_FILE.read_text(encoding="utf-8"))
        else:
            data = {"ultimo_numero": 0}
        data["ultimo_numero"] = int(data.get("ultimo_numero", 0)) + 1
        _COUNTER_FILE.write_text(json.dumps(data), encoding="utf-8")
        return str(data["ultimo_numero"])
    finally:
        _liberar_lock_contador(fd)


# =============================================================================
# MONTAGEM DA DPS
# =============================================================================
def _montar_xml_dps(
    empresa: Empresa,
    servico: DadosServico,
    endereco_tomador: dict[str, str],
    invoice: dict[str, Any],
    numero_dps: str,
) -> tuple[str, str]:
    """Monta o XML da DPS no padrão nacional CGNFS-e.

    Returns:
        (xml_str, dps_id) — o Id é usado para a referência da assinatura.
    """
    # Código IBGE do município do prestador (Brasília=5300108)
    cod_mun_prestador = (settings.nfse_codigo_municipio_emissor or "5300108").strip()

    # Município do tomador — usa o do prestador como fallback
    cod_mun_tomador = _codigo_ibge_por_cidade(
        endereco_tomador["cidade"], endereco_tomador["uf"], default=cod_mun_prestador
    )

    # Monta o Id da DPS conforme XSD v1.01 (TSIdDPS, 45 chars):
    # "DPS" + Cód.Mun(7) + Tipo Insc.Fed(1) + Insc.Fed(14) + Série(5) + Núm.DPS(15)
    # Para CNPJ, Tipo Inscricao Federal = 2
    cnpj_limpo = _so_digitos(settings.nfse_cnpj_prestador).zfill(14)
    serie_padded = (settings.nfse_serie_padrao or "00001").zfill(5)[:5]
    numero_padded = numero_dps.zfill(15)[:15]
    dps_id = f"DPS{cod_mun_prestador.zfill(7)}2{cnpj_limpo}{serie_padded}{numero_padded}"

    # Enum do ambiente (1=prod, 2=homologação)
    tp_amb = (
        ts.TstipoAmbiente.VALUE_1
        if settings.nfse_ambiente == "producao"
        else ts.TstipoAmbiente.VALUE_2
    )

    # ---------- Prestador ----------
    # regTrib (B-40) é OBRIGATÓRIO pelo schema — opSimpNac e regEspTrib são 1-1
    def _enum_or_none(enum_cls, valor: int):
        """Converte um inteiro para o membro VALUE_n do enum, ou None se inválido."""
        try:
            return getattr(enum_cls, f"VALUE_{valor}")
        except AttributeError:
            return None

    reg_trib = tc.TcregTrib(
        opSimpNac=_enum_or_none(ts.TsopSimpNac, settings.nfse_op_simples_nacional),
        regApTribSN=_enum_or_none(
            ts.TsregimeApuracaoSimpNac, settings.nfse_regime_apuracao_sn
        ),
        regEspTrib=_enum_or_none(ts.TsregEspTrib, settings.nfse_regime_especial_trib),
    )

    prest = tc.TcinfoPrestador(
        CNPJ=_so_digitos(settings.nfse_cnpj_prestador),
        IM=_so_digitos(settings.nfse_inscricao_municipal),
        xNome=settings.nfse_razao_social_prestador,
        regTrib=reg_trib,
    )

    # ---------- Tomador ----------
    endereco_tom = tc.Tcendereco(
        endNac=tc.TcenderNac(
            cMun=cod_mun_tomador,
            CEP=endereco_tomador["cep"][:8],
        ),
        xLgr=endereco_tomador["logradouro"][:255] or None,
        nro=endereco_tomador["numero"][:60] or None,
        xCpl=endereco_tomador["complemento"][:156] or None,
        xBairro=endereco_tomador["bairro"][:60] or None,
    )
    toma = tc.TcinfoPessoa(
        CNPJ=empresa.cnpj,
        IM=None,
        xNome=empresa.razao_social,
        end=endereco_tom,
        email=empresa.email or None,
    )

    # ---------- Serviço ----------
    # cTribNac: 6 dígitos numéricos (ex: "010700" para item 01.07 LC116)
    c_trib_nac = _normalizar_cTribNac(servico.codigo_servico)
    # cTribMun: código de tributação municipal (obrigatório, xsd:int até 10 dígitos)
    c_trib_mun = settings.nfse_codigo_trib_municipal
    # cNBS: código NBS (9 dígitos, opcional mas recomendado — presente nas NFs reais)
    c_nbs = settings.nfse_nbs_padrao or None

    serv = tc.Tcserv(
        locPrest=tc.TclocPrest(
            cLocPrestacao=cod_mun_prestador,
            # opConsumServ REMOVIDO — campo não existe no XSD v1.01
            # (era do v1.00, removido na Reforma Tributária)
        ),
        cServ=tc.Tccserv(
            cTribNac=c_trib_nac,
            cTribMun=c_trib_mun,
            xDescServ=servico.descricao[:2000],
            cNBS=c_nbs,
        ),
    )

    # ---------- Valores ----------
    # totTrib (B-228) é OBRIGATÓRIO — Lei 12.741/2012 exige indicar carga tributária
    # TCTribTotal é um <xsd:choice> — só pode conter UM dos seguintes:
    #   vTotTrib | pTotTrib | indTotTrib | pTotTribSN
    # Para Simples Nacional usamos APENAS pTotTribSN (% da alíquota efetiva do DAS).
    # NÃO combinar com indTotTrib (são mutuamente exclusivos no XSD).
    tot_trib = tc.TctribTotal(
        pTotTribSN=f"{settings.nfse_percentual_tributos_sn:.2f}",
    )

    valores = tc.TcinfoValores(
        vServPrest=tc.TcvservPrest(
            vServ=_decimal_2(servico.valor_cents),
        ),
        trib=tc.TcinfoTributacao(
            tribMun=tc.TctribMunicipal(
                tribISSQN=ts.TstribIssqn.VALUE_1,  # 1=Operação tributável
                # tpImunidade: NÃO informar quando tribISSQN=1 (só para imunidade)
                tpRetISSQN=ts.TstipoRetIssqn.VALUE_1,  # 1=ISS não retido
                pAliq=f"{servico.aliquota_iss:.2f}",
            ),
            totTrib=tot_trib,
        ),
    )

    # ---------- Monta o DPS (base v1.00 via nfelib) ----------
    info_dps = tc.TcinfDps(
        tpAmb=tp_amb,
        dhEmi=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        verAplic="iugu-nfse-df-0.3",
        serie=settings.nfse_serie_padrao,
        nDPS=numero_dps,
        dCompet=date.today().isoformat(),
        tpEmit=ts.TsemitenteDps.VALUE_1,  # 1 = prestador
        cLocEmi=cod_mun_prestador,
        prest=prest,
        toma=toma,
        serv=serv,
        valores=valores,
        Id=dps_id,
    )
    dps = Dps(infDPS=info_dps, versao="1.00")
    xml_v100 = dps.to_xml()

    # ---------- Patch para v1.01: injetar IBSCBS via lxml ----------
    xml_v101 = _patch_xml_para_v101(xml_v100, empresa, servico, invoice)
    return xml_v101, dps_id


def _codigo_ibge_por_cidade(cidade: str, uf: str, default: str) -> str:
    """Mapeamento mínimo — expanda conforme necessário ou use uma API IBGE.

    Para o DF, todas as cidades (e Brasília) compartilham o código 5300108.
    Para outras UFs, adicione conforme preciso.
    """
    cidade_norm = (cidade or "").strip().upper()
    uf_norm = (uf or "").strip().upper()

    # Distrito Federal — DF tem um único município (Brasília)
    if uf_norm == "DF":
        return "5300108"

    # Atalhos para municípios comuns (expanda conforme demanda)
    atalhos = {
        ("SÃO PAULO", "SP"): "3550308",
        ("SAO PAULO", "SP"): "3550308",
        ("RIO DE JANEIRO", "RJ"): "3304557",
        ("BELO HORIZONTE", "MG"): "3106200",
        ("GOIÂNIA", "GO"): "5208707",
        ("GOIANIA", "GO"): "5208707",
    }
    return atalhos.get((cidade_norm, uf_norm), default)


# =============================================================================
# PATCH v1.00 → v1.01 (injeção do grupo IBSCBS via lxml)
# =============================================================================
NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"


def _patch_xml_para_v101(
    xml_v100: str,
    empresa: Empresa,
    servico: DadosServico,
    invoice: dict[str, Any],
) -> str:
    """Transforma o XML v1.00 gerado pela nfelib em v1.01.

    Passos:
        1. Muda atributo versao da <DPS> de "1.00" para "1.01"
        2. Remove <opConsumServ> de <locPrest> (inexistente no XSD v1.01)
        3. Corrige <totTrib> (choice exclusivo — remove filhos extras)
        4. Injeta o grupo <IBSCBS> obrigatório depois de <valores> dentro de <infDPS>
        5. Corrige a ordem dos filhos de <tribMun> conforme XSD v1.01
    """
    from lxml import etree

    root = etree.fromstring(xml_v100.encode("utf-8"))
    ns = {"nfse": NFSE_NS}

    # 1. Atualizar versao="1.00" → "1.01" na tag <DPS>
    dps_el = root if root.tag == f"{{{NFSE_NS}}}DPS" else root.find(f".//{{{NFSE_NS}}}DPS")
    if dps_el is None:
        # Talvez sem namespace (nfelib pode gerar sem prefixo)
        dps_el = root if "DPS" in root.tag else root.find(".//DPS")
    if dps_el is not None and dps_el.get("versao") == "1.00":
        dps_el.set("versao", "1.01")

    # 2. Remover <opConsumServ> de <locPrest> — campo inexistente no XSD v1.01
    for op_el in root.iter(f"{{{NFSE_NS}}}opConsumServ"):
        op_el.getparent().remove(op_el)
        logger.debug("Removido <opConsumServ> (inexistente no XSD v1.01)")
    # Também sem namespace (nfelib pode gerar sem)
    for op_el in root.iter("opConsumServ"):
        op_el.getparent().remove(op_el)

    # 3. Corrigir <totTrib> — é um choice, não pode ter mais de um filho
    #    Para SN, manter APENAS <pTotTribSN>, remover <indTotTrib> se ambos presentes
    for tot_trib in list(root.iter(f"{{{NFSE_NS}}}totTrib")) + list(root.iter("totTrib")):
        ptot_sn = tot_trib.find(f"{{{NFSE_NS}}}pTotTribSN")
        if ptot_sn is None:
            ptot_sn = tot_trib.find("pTotTribSN")
        if ptot_sn is not None:
            # Se pTotTribSN existe, remover todos os outros (indTotTrib, vTotTrib, pTotTrib)
            for tag_remover in ["indTotTrib", "vTotTrib", "pTotTrib"]:
                for el in [tot_trib.find(f"{{{NFSE_NS}}}{tag_remover}"),
                           tot_trib.find(tag_remover)]:
                    if el is not None:
                        tot_trib.remove(el)
                        logger.debug(f"Removido <{tag_remover}> de <totTrib> (choice exclusivo com pTotTribSN)")

    # 4. Encontrar <infDPS> e <valores> para injetar <IBSCBS> logo depois de <valores>
    inf_dps = root.find(f".//{{{NFSE_NS}}}infDPS")
    if inf_dps is None:
        inf_dps = root.find(".//infDPS")

    # Localizar o elemento <valores> (o IBSCBS vem imediatamente após)
    valores_el = inf_dps.find(f"{{{NFSE_NS}}}valores") if inf_dps is not None else None
    if valores_el is None and inf_dps is not None:
        valores_el = inf_dps.find("valores")

    if inf_dps is not None and valores_el is not None:
        # Construir o elemento <IBSCBS>
        ibscbs_el = _montar_ibscbs_element(empresa, servico, invoice)

        # Inserir logo depois de <valores>
        valores_index = list(inf_dps).index(valores_el)
        inf_dps.insert(valores_index + 1, ibscbs_el)

    # 5. Corrigir ordem dos filhos de <tribMun> (nfelib v1.00 gera em ordem diferente da v1.01)
    _corrigir_ordem_tribMun(root)

    return etree.tostring(root, encoding="unicode", xml_declaration=False)


def _corrigir_ordem_tribMun(root) -> None:
    """Reordena os filhos de <tribMun> para a ordem exigida pelo XSD v1.01.

    Ordem correta (TCTribMunicipal):
        tribISSQN → cPaisResult? → tpImunidade? → exigSusp? → BM? → tpRetISSQN → pAliq?

    A nfelib v1.00 pode gerar pAliq antes de tpRetISSQN, o que viola o schema v1.01.
    Também remove tpImunidade se tribISSQN != 2 (imunidade).
    """
    from lxml import etree

    # Ordem definida pelo XSD v1.01 para TCTribMunicipal
    ORDEM_TRIBMUN = [
        "tribISSQN", "cPaisResult", "tpImunidade", "exigSusp", "BM",
        "tpRetISSQN", "pAliq",
    ]

    for trib_mun in root.iter():
        localname = etree.QName(trib_mun).localname
        if localname != "tribMun":
            continue

        filhos = list(trib_mun)
        if not filhos:
            continue

        # Detectar o namespace dos filhos
        ns = etree.QName(filhos[0]).namespace or ""
        prefix = f"{{{ns}}}" if ns else ""

        # Verificar se tribISSQN != 2 e remover tpImunidade se presente
        trib_issqn_el = trib_mun.find(f"{prefix}tribISSQN")
        if trib_issqn_el is not None and trib_issqn_el.text != "2":
            tp_imunidade = trib_mun.find(f"{prefix}tpImunidade")
            if tp_imunidade is not None:
                trib_mun.remove(tp_imunidade)
                filhos = list(trib_mun)  # atualizar lista

        # Reordenar conforme XSD v1.01
        def _sort_key(el):
            name = etree.QName(el).localname
            try:
                return ORDEM_TRIBMUN.index(name)
            except ValueError:
                return 999  # elementos desconhecidos vão pro final

        filhos_ordenados = sorted(filhos, key=_sort_key)

        # Verificar se a ordem mudou
        nomes_antes = [etree.QName(f).localname for f in filhos]
        nomes_depois = [etree.QName(f).localname for f in filhos_ordenados]
        if nomes_antes != nomes_depois:
            logger.debug(f"Reordenando tribMun: {nomes_antes} → {nomes_depois}")
            for filho in filhos:
                trib_mun.remove(filho)
            for filho in filhos_ordenados:
                trib_mun.append(filho)


def _montar_ibscbs_element(
    empresa: Empresa,
    servico: DadosServico,
    invoice: dict[str, Any],
) -> "etree.Element":
    """Constrói o elemento <IBSCBS> conforme schema v1.01 do CGNFS-e.

    Estrutura mínima obrigatória (para Simples Nacional, serviço TI regular):
        <IBSCBS xmlns="...nfse">
            <finNFSe>0</finNFSe>              (NFS-e regular)
            <indFinal>0</indFinal>             (não é consumo pessoal)
            <cIndOp>XXXXXX</cIndOp>            (tabela IndOp_IBSCBS)
            <indDest>0</indDest>               (tomador = destinatário)
            <valores>
                <trib>
                    <gIBSCBS>
                        <CST>XXX</CST>         (tabela CST IBSCBS)
                        <cClassTrib>XXXXXX</cClassTrib>  (tabela cClassTrib)
                    </gIBSCBS>
                </trib>
            </valores>
        </IBSCBS>

    Nota: Os valores de cIndOp, CST e cClassTrib vêm de settings (config.py/.env).
    Para produção devem ser validados com o contador. Para homologação, usamos
    valores provisórios que passem na validação do schema.
    """
    from lxml import etree

    def _sub(parent, tag, text=None):
        """Cria subelemento com namespace NFS-e."""
        el = etree.SubElement(parent, f"{{{NFSE_NS}}}{tag}")
        if text is not None:
            el.text = str(text)
        return el

    # Raiz <IBSCBS>
    ibscbs = etree.Element(f"{{{NFSE_NS}}}IBSCBS")

    # Campos diretos obrigatórios
    _sub(ibscbs, "finNFSe", "0")         # 0 = NFS-e regular
    _sub(ibscbs, "indFinal", "0")        # 0 = Não é consumo pessoal
    _sub(ibscbs, "cIndOp", settings.nfse_ibscbs_cIndOp.zfill(6)[:6])
    # tpOper é opcional — só informar se tpEnteGov for informado ou serviços sobre imóveis
    # gRefNFSe é opcional — só quando tpOper = 2 ou 3
    # tpEnteGov é opcional — só para administração pública direta
    _sub(ibscbs, "indDest", "0")         # 0 = tomador = destinatário
    # dest é opcional — só quando indDest = 1
    # imovel é opcional — só para operações com imóveis

    # Grupo <valores> (obrigatório dentro de IBSCBS)
    valores = _sub(ibscbs, "valores")
    # gReeRepRes é opcional — reembolso/repasse/ressarcimento (não se aplica)

    # Grupo <trib> (obrigatório dentro de valores)
    trib = _sub(valores, "trib")

    # Grupo <gIBSCBS> (obrigatório dentro de trib) — classificação tributária
    g_ibscbs = _sub(trib, "gIBSCBS")
    _sub(g_ibscbs, "CST", settings.nfse_ibscbs_cst.zfill(3)[:3])
    _sub(g_ibscbs, "cClassTrib", settings.nfse_ibscbs_cClassTrib.zfill(6)[:6])
    # cCredPres é opcional — crédito presumido (não se aplica para SN)
    # gTribRegular é opcional — tributação regular (não se aplica para SN se CST=900)
    # gDif é opcional — diferimento (não se aplica)

    return ibscbs


# =============================================================================
# ASSINATURA DIGITAL (certificado A1 .pfx)
# =============================================================================
def _assinar_xml(xml_str: str, dps_id: str) -> str:
    """Assina a DPS com certificado A1 usando erpbrasil.assinatura.

    IMPORTANTE:
    - `Certificado` aceita o CAMINHO do arquivo (string) OU bytes em base64.
      Se passar bytes binários puros, ela tenta decodificar como base64 e corrompe.
      Por isso passamos o path via str(cert_path).
    - `assina_xml2` espera um Element do lxml (não string), então parseamos antes.
    """
    from lxml import etree

    cert_path = Path(settings.nfse_certificado_path)
    if not cert_path.exists():
        raise FileNotFoundError(f"Certificado A1 não encontrado em {cert_path}")

    certificado = _AssinaturaCertificado(
        arquivo=str(cert_path.resolve()),
        senha=settings.nfse_certificado_senha,
    )
    assinador = _Assinatura(certificado=certificado)

    # Parseia o XML em um Element do lxml (exigência do assina_xml2)
    root = etree.fromstring(xml_str.encode("utf-8"))
    xml_assinado = assinador.assina_xml2(root, dps_id)

    # O retorno pode ser bytes, string ou Element — normalizamos para string
    if isinstance(xml_assinado, bytes):
        return xml_assinado.decode("utf-8")
    if hasattr(xml_assinado, "tag"):  # lxml Element
        return etree.tostring(xml_assinado, encoding="unicode", xml_declaration=False)
    return str(xml_assinado)


# =============================================================================
# ENVIO AO WEBSERVICE (SOAP Document/Literal wrapped)
# =============================================================================
# Conforme Manual de Integração v1.01 do NFS-e Padrão Nacional (17/03/2026):
# - Padrão SOAP 1.1 com Style/Encoding "Document/Literal, wrapped"
# - 3 operações principais (usamos GerarNfse por ser síncrona e aceitar 1 DPS):
#     • GerarNfse                — síncrona, 1 DPS → 1 NFS-e
#     • RecepcionarLoteDpsSincrono — síncrona, lote de DPS
#     • RecepcionarLoteDps       — assíncrona, retorna protocolo pra consulta
# - Namespace do serviço: http://www.sped.fazenda.gov.br/nfse
# =============================================================================

# Operação default — a mais simples e síncrona
NFSE_OPERACAO_PADRAO = "GerarNfse"
NFSE_NAMESPACE = "http://www.sped.fazenda.gov.br/nfse"


def _enviar_ao_webservice(xml_assinado: str, endpoint: str) -> tuple[str, int, str]:
    """Envia o XML ao webservice do ISS DF.

    Detecta protocolo via settings.nfse_ws_protocolo:
        - "soap": envia dentro de um envelope SOAP 1.1 (Document/Literal wrapped)
        - "rest": envia como raw XML em POST

    Returns:
        (corpo_da_resposta, status_code_http, envelope_enviado)
    """
    protocolo = (settings.nfse_ws_protocolo or "soap").lower()

    cert_path = Path(settings.nfse_certificado_path)
    senha = settings.nfse_certificado_senha
    cert_file, key_file = _pkcs12_to_pem_tempfiles(cert_path, senha)

    try:
        if protocolo == "soap":
            operacao = NFSE_OPERACAO_PADRAO
            envelope = _envelopar_soap(xml_assinado, operacao)
            headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{NFSE_NAMESPACE}/{operacao}"',
            }
            body = envelope
        else:
            headers = {"Content-Type": "application/xml; charset=utf-8"}
            body = xml_assinado
            envelope = xml_assinado

        logger.info(f"Enviando DPS ({protocolo} — {NFSE_OPERACAO_PADRAO}) para {endpoint}")
        with httpx.Client(
            cert=(str(cert_file), str(key_file)) if cert_file.exists() else None,
            timeout=60.0,
            verify=True,
        ) as client:
            response = client.post(endpoint, content=body.encode("utf-8"), headers=headers)

        return response.text, response.status_code, envelope
    finally:
        # Material sensível (chave privada A1) — não silenciar falha de unlink.
        # Logamos como WARNING para auditoria, mesmo padrão do _enviar_soap_abrasf
        # (Fix 2.1 — defesa em profundidade).
        for f in (cert_file, key_file):
            try:
                if f and f.exists():
                    f.unlink()
            except Exception as exc:
                logger.warning(
                    f"Falha ao remover PEM temporário {f}: {exc}"
                )


def _pkcs12_to_pem_tempfiles(pfx_path: Path, senha: str) -> tuple[Path, Path]:
    """Extrai certificado + chave privada do .pfx em arquivos PEM temporários.

    Necessário porque httpx usa cert=(certfile, keyfile) e não aceita pkcs12 direto.
    Os arquivos temporários devem ser removidos pelo caller.

    Hardening (Fix 2.1 — appsec):
      - `tempfile.mkstemp()` em vez de `mktemp()`: criação ATÔMICA do arquivo
        com permissões restritas (sem janela TOCTOU em que outro processo
        pudesse criar o arquivo antes de nós).
      - `os.chmod(0o600)` aplicado em POSIX após gravação (mkstemp já cria
        com 0o600 no Linux, mas reforçamos por defesa em profundidade; em
        Windows o chmod é no-op funcional — a proteção vem de %TEMP% ACL).
      - A chave privada é serializada com `NoEncryption()` porque o httpx
        (cliente mTLS) não consegue ler chave criptografada por padrão. A
        segurança da chave em disco depende portanto de:
          (a) permissão 0o600 do arquivo PEM e
          (b) remoção imediata após o request (responsabilidade do caller).
    """
    import os as _os
    import tempfile
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    with open(pfx_path, "rb") as f:
        pkcs12_bytes = f.read()

    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        pkcs12_bytes, senha.encode() if senha else None
    )

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    def _criar_pem_seguro(suffix: str, payload: bytes) -> Path:
        # mkstemp cria o arquivo de forma atômica (O_CREAT|O_EXCL|O_RDWR);
        # devolve fd já aberto que precisamos fechar após escrever.
        fd, path_str = tempfile.mkstemp(suffix=suffix)
        try:
            _os.write(fd, payload)
        finally:
            _os.close(fd)
        # POSIX: 0o600. Em Windows os.chmod só altera bit read-only; a proteção
        # real vem das ACLs do diretório %TEMP% do usuário. Mantemos o chmod
        # incondicional porque é no-op seguro no Windows.
        try:
            _os.chmod(path_str, 0o600)
        except OSError:
            # Não-fatal: o material continua restrito ao diretório do usuário.
            pass
        return Path(path_str)

    cert_file = _criar_pem_seguro("_cert.pem", cert_pem)
    key_file = _criar_pem_seguro("_key.pem", key_pem)
    return cert_file, key_file


def _envelopar_soap(
    xml_body: str,
    operacao: str = NFSE_OPERACAO_PADRAO,
    versao_dados: str = "1.01",
    usar_cdata: bool = False,
) -> str:
    """Embrulha o DPS assinado em envelope SOAP 1.1 (padrão ABRASF/ISSNet v1.01).

    Estrutura exigida pelo Manual de Integração v1.01 — seção 7.4.1 (Área do
    Cabeçalho). O webservice do ISSNet exige DOIS parâmetros na chamada:

        nfseCabecMsg  → cabeçalho com a versão do leiaute
        nfseDadosMsg  → dados propriamente ditos (lote/DPS/consulta)

    Args:
        xml_body: XML assinado da DPS (com Signature já embutida)
        operacao: "GerarNfse", "RecepcionarLoteDpsSincrono", etc.
        versao_dados: versão do schema (default "1.00")
        usar_cdata: se True, embrulha com CDATA (padrão ABRASF antigo);
                    se False (default), usa XML aninhado (padrão ISSNet v1.01).
    """
    # Remove declaração XML do corpo interno (se veio com <?xml ...?>)
    xml_body_clean = re.sub(r'<\?xml[^?]*\?>\s*', '', xml_body).strip()

    # Mapa operação → nome do wrapper Envio
    wrappers = {
        "GerarNfse": "GerarNfseEnvio",
        "RecepcionarLoteDpsSincrono": "EnviarLoteDpsSincronoEnvio",
        "RecepcionarLoteDps": "EnviarLoteDpsEnvio",
        "CancelarNfse": "CancelarNfseEnvio",
        "ConsultarNfseDps": "ConsultarNfseDpsEnvio",
        "ConsultarNfsePorFaixa": "ConsultarNfseFaixaEnvio",
    }
    wrapper = wrappers.get(operacao, f"{operacao}Envio")

    # Cabeçalho (obrigatório conforme seção 7.4.1 do manual)
    cabecalho_xml = (
        f'<cabecalho versao="{versao_dados}" xmlns="{NFSE_NAMESPACE}">'
        f'<versaoDados>{versao_dados}</versaoDados>'
        f'</cabecalho>'
    )

    # Dados (DPS assinada dentro do wrapper da operação)
    dados_xml = (
        f'<{wrapper} xmlns="{NFSE_NAMESPACE}">'
        f'{xml_body_clean}'
        f'</{wrapper}>'
    )

    if usar_cdata:
        cabec_content = f'<![CDATA[{cabecalho_xml}]]>'
        dados_content = f'<![CDATA[{dados_xml}]]>'
    else:
        cabec_content = cabecalho_xml
        dados_content = dados_xml

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\n'
        '  <soap:Body>\n'
        f'    <{operacao} xmlns="{NFSE_NAMESPACE}">\n'
        f'      <nfseCabecMsg>{cabec_content}</nfseCabecMsg>\n'
        f'      <nfseDadosMsg>{dados_content}</nfseDadosMsg>\n'
        f'    </{operacao}>\n'
        '  </soap:Body>\n'
        '</soap:Envelope>\n'
    )


# =============================================================================
# PARSING DA RESPOSTA
# =============================================================================
def _parsear_resposta(xml_resposta: str) -> dict[str, Any]:
    """Extrai informações da resposta do webservice (GerarNfseResposta).

    Estrutura esperada conforme Manual v1.01:
        GerarNfseResposta
          ├── ListaNfse              (sucesso — pelo menos 1 NFS-e aprovada)
          │     └── CompNfse
          │           └── Nfse
          │                 └── infNFSe
          │                       ├── nNFSe          ← número
          │                       ├── cCodVerif      ← código verificação
          │                       ├── dhProc
          │                       └── (demais campos da nota aprovada)
          ├── ListaMensagemAlertaRetorno (avisos não-fatais)
          │     └── MensagemRetorno
          │           ├── Codigo
          │           ├── Mensagem
          │           └── Correcao
          └── ListaMensagemRetorno   (erros — NFS-e rejeitada)
                └── MensagemRetorno
                      ├── Codigo
                      ├── Mensagem
                      └── Correcao

    O SOAP envelope é transparentemente desconsiderado pela busca por localname.
    """
    from lxml import etree

    try:
        root = etree.fromstring(xml_resposta.encode("utf-8"))
    except Exception as exc:
        return {
            "sucesso": False,
            "mensagem_erro": f"Retorno não é XML válido: {exc}",
            "mensagens": [],
        }

    def _iter_localname(tag: str):
        for el in root.iter():
            if etree.QName(el).localname == tag:
                yield el

    def _find_text(tag: str) -> Optional[str]:
        for el in _iter_localname(tag):
            if el.text:
                return el.text.strip()
        return None

    # 1. Número da NFS-e aprovada (vem dentro de ListaNfse > CompNfse > Nfse > infNFSe)
    numero_nfse = _find_text("nNFSe")
    codigo_verif = _find_text("cCodVerif")

    # 2. Coleta mensagens (rejeição + alertas)
    mensagens_erro = []
    mensagens_alerta = []

    # ListaMensagemRetorno → rejeições
    for lista in _iter_localname("ListaMensagemRetorno"):
        for msg in lista.iter():
            if etree.QName(msg).localname == "MensagemRetorno":
                codigo = _text_child(msg, "Codigo")
                texto = _text_child(msg, "Mensagem")
                correcao = _text_child(msg, "Correcao")
                if texto:
                    full = f"[{codigo or '?'}] {texto}"
                    if correcao:
                        full += f" — Correção: {correcao}"
                    mensagens_erro.append(full)

    # ListaMensagemAlertaRetorno → avisos (não-fatais)
    for lista in _iter_localname("ListaMensagemAlertaRetorno"):
        for msg in lista.iter():
            if etree.QName(msg).localname == "MensagemRetorno":
                codigo = _text_child(msg, "Codigo")
                texto = _text_child(msg, "Mensagem")
                if texto:
                    mensagens_alerta.append(f"[{codigo or '?'}] {texto}")

    # 3. Determina sucesso: NFS-e número presente = aprovado
    sucesso = bool(numero_nfse)

    # Consolida mensagens: erros primeiro, alertas depois
    todas_mensagens = mensagens_erro + mensagens_alerta

    # 4. Identifica mensagem de erro principal (se rejeitado)
    mensagem_erro = None
    if not sucesso:
        if mensagens_erro:
            mensagem_erro = mensagens_erro[0]
        elif mensagens_alerta:
            mensagem_erro = mensagens_alerta[0]
        else:
            # Fallback: talvez o retorno seja SOAP Fault
            fault_string = _find_text("faultstring") or _find_text("Reason")
            mensagem_erro = fault_string or "NFS-e não foi aprovada (sem mensagem explícita)"

    return {
        "sucesso": sucesso,
        "numero_nfse": numero_nfse,
        "codigo_verificacao": codigo_verif,
        "mensagens": todas_mensagens,
        "mensagens_erro": mensagens_erro,
        "mensagens_alerta": mensagens_alerta,
        "mensagem_erro": mensagem_erro,
    }


def _text_child(element, localname: str) -> Optional[str]:
    """Helper: retorna o texto de um filho direto pelo localname."""
    from lxml import etree
    for child in element.iter():
        if etree.QName(child).localname == localname and child.text:
            return child.text.strip()
    return None


# =============================================================================
# ARQUIVAMENTO EM DISCO
# =============================================================================
def _arquivar(xml_str: str, prefix: str, suffix: str) -> Path:
    """Salva o XML em /nfse_emitidas/ com timestamp. Retorna o caminho."""
    Path(settings.nfse_output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{prefix}_{suffix}_{ts}.xml"
    caminho = Path(settings.nfse_output_dir) / fname
    caminho.write_text(xml_str, encoding="utf-8")
    return caminho


def _gravar_log_nfse(
    invoice: dict[str, Any],
    empresa: Empresa,
    resultado: ResultadoEmissao,
    rps_numero: Optional[int | str] = None,
) -> Optional[Path]:
    """Grava o log `nfse_<invoice_id>.json` que a API lê para detectar a nota emitida.

    Por que existe: os backends de emissão só arquivavam os XMLs (rps_*/dps_*). As
    funções de detecção em `api_routes.py` (`_carregar_mapa_nfse`,
    `_buscar_nfse_da_fatura`, `_check_nfse`, `listar_nfse`) casam a NFS-e à fatura
    lendo arquivos `*.json` e comparando o campo `invoice_id` — sem este arquivo a
    listagem/detalhe/dashboard nunca reconheciam a nota. Esta função fecha o gap.

    Chamada SOMENTE em sucesso real (não em dry-run nem falha), por ambos os backends
    (`_emitir_abrasf204` e `_emitir_nacional`), logo após `resultado.sucesso == True`.

    Resiliente por design: se a gravação falhar, apenas loga `warning` e devolve None.
    O XML já foi arquivado e a NFS-e já foi aceita pelo ISS — o log é só o índice da
    API, então uma falha aqui jamais pode derrubar a emissão.

    As chaves gravadas espelham exatamente o que os leitores consomem:
        - invoice_id ......... chave de casamento fatura<->nota (todos os leitores)
        - numero_nfse ........ listar_faturas/detalhe/listar_nfse
        - codigo_verificacao . detalhe + e-mail (enviar_nfse_email)
        - cnpj / razao_social  listar_nfse (cnpj_tomador, razao_social)
        - valor .............. listar_nfse (R$ em reais, 2 casas)
        - data_emissao ....... detalhe/listar_nfse (ISO YYYY-MM-DD)
        - sucesso ............ filtro de sucesso em todos
        - xml_enviado_path /   enviar_nfse_email anexa os XMLs no reenvio
          xml_retorno_path /   (pdf_path hoje é sempre None — DANFSE oficial futuro)
          pdf_path
        - rps_numero / padrao / ambiente .... extras úteis para auditoria
    """
    invoice_id = invoice.get("id") or ""
    if not invoice_id:
        logger.warning("[NFS-e log] invoice sem 'id' — log .json não será gravado")
        return None

    # Valor em reais (os leitores exibem 'valor' como R$ com 2 casas).
    total_cents = int(invoice.get("total_paid_cents") or invoice.get("total_cents") or 0)
    valor_reais = round(total_cents / 100.0, 2)

    log = {
        "invoice_id": invoice_id,
        "numero_nfse": resultado.numero_nfse,
        "codigo_verificacao": resultado.codigo_verificacao,
        "cnpj": empresa.cnpj,
        "razao_social": empresa.razao_social,
        "valor": valor_reais,
        "data_emissao": date.today().isoformat(),
        "sucesso": True,
        # Caminhos dos artefatos (string), reusados pelo reenvio de e-mail.
        "xml_enviado_path": str(resultado.xml_enviado_path) if resultado.xml_enviado_path else None,
        "xml_retorno_path": str(resultado.xml_retorno_path) if resultado.xml_retorno_path else None,
        "pdf_path": str(resultado.pdf_path) if resultado.pdf_path else None,
        # Extras de auditoria.
        "rps_numero": str(rps_numero) if rps_numero is not None else None,
        "padrao": settings.nfse_padrao,
        "ambiente": resultado.ambiente or settings.nfse_ambiente,
    }

    try:
        Path(settings.nfse_output_dir).mkdir(parents=True, exist_ok=True)
        caminho = Path(settings.nfse_output_dir) / f"nfse_{invoice_id}.json"
        # UTF-8 sem BOM; ensure_ascii=False para preservar acentos da razão social.
        caminho.write_text(
            json.dumps(log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[NFS-e log] Índice gravado: {caminho.name} (invoice {invoice_id})")
        return caminho
    except Exception as exc:
        # Nunca derruba a emissão: o XML já foi arquivado e a nota já foi aceita.
        logger.warning(
            f"[NFS-e log] Falha ao gravar índice nfse_{invoice_id}.json: {exc} "
            f"(emissão NÃO afetada — XML já arquivado)"
        )
        return None


# =============================================================================
# Compatibilidade: exporta tudo que o webhook_server espera
# =============================================================================
__all__ = [
    "emitir_nfse",
    "extrair_endereco_tomador",
    "DadosServico",
    "ResultadoEmissao",
]
