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
# FUNÇÃO PRINCIPAL — chamada pelo webhook_server
# =============================================================================
async def emitir_nfse(invoice: dict[str, Any], empresa: Empresa) -> dict[str, Any]:
    """Emite uma NFS-e no DF para uma fatura já paga da Iugu.

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

        # 9. Gerar PDF customizado (opcional — falha não interrompe o fluxo)
        try:
            from .pdf_nfse import gerar_pdf_nfse

            pdf_path = Path(settings.nfse_output_dir) / f"NFS-e_{resultado.numero_nfse}.pdf"

            # Preparar dados para o PDF
            valor_formatado = f"R$ {servico.valor_cents / 100:.2f}".replace('.', ',')
            endereco_tomador_str = (
                f"{endereco_tomador.get('logradouro', '')} "
                f"{endereco_tomador.get('numero', '')} "
                f"{endereco_tomador.get('bairro', '')}, "
                f"{endereco_tomador.get('cidade', '')} - "
                f"{endereco_tomador.get('uf', '')}"
            )

            # URL para validação (construída a partir do número da NFS-e)
            url_validacao = (
                f"https://iss.fazenda.df.gov.br/online/consultarNFSe.aspx?"
                f"id={resultado.numero_nfse}&cod={resultado.codigo_verificacao}"
            )

            sucesso_pdf = gerar_pdf_nfse(
                pdf_path=pdf_path,
                numero_nfse=resultado.numero_nfse,
                serie=settings.nfse_serie_padrao,
                data_emissao=datetime.now().strftime('%d/%m/%Y'),
                codigo_verificacao=resultado.codigo_verificacao,
                tomador_nome=empresa.razao_social,
                tomador_cnpj=empresa.cnpj,
                tomador_endereco=endereco_tomador_str,
                descricao_servico=servico.descricao[:100],  # Limitar a 100 chars
                valor_servico=valor_formatado,
                aliquota_iss=servico.aliquota_iss,
                prestador_nome=settings.nfse_razao_social_prestador,
                prestador_cnpj=settings.nfse_cnpj_prestador,
                url_validacao=url_validacao,
            )

            if sucesso_pdf:
                resultado.pdf_path = pdf_path
                logger.info(f"[NFS-e PDF] Gerado com sucesso: {pdf_path}")
            else:
                logger.warning(f"[NFS-e PDF] Falha ao gerar PDF (continuando sem PDF)")

        except ImportError:
            logger.warning("Módulo pdf_nfse não disponível — PDF não será gerado")
        except Exception as exc:
            logger.warning(f"Falha ao gerar PDF da NFS-e: {exc} (continuando sem PDF)")

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
        for f in (cert_file, key_file):
            try:
                if f and f.exists():
                    f.unlink()
            except Exception:
                pass


def _pkcs12_to_pem_tempfiles(pfx_path: Path, senha: str) -> tuple[Path, Path]:
    """Extrai certificado + chave privada do .pfx em arquivos PEM temporários.

    Necessário porque httpx usa cert=(certfile, keyfile) e não aceita pkcs12 direto.
    Os arquivos temporários devem ser removidos pelo caller.
    """
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

    cert_file = Path(tempfile.mktemp(suffix="_cert.pem"))
    key_file = Path(tempfile.mktemp(suffix="_key.pem"))
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)
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


# =============================================================================
# Compatibilidade: exporta tudo que o webhook_server espera
# =============================================================================
__all__ = [
    "emitir_nfse",
    "extrair_endereco_tomador",
    "DadosServico",
    "ResultadoEmissao",
]
