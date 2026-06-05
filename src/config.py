"""
Carrega e valida todas as configurações do sistema a partir do arquivo .env.

Uso:
    from src.config import settings
    print(settings.iugu_api_token)
"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Diretório raiz do projeto (pai da pasta src/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configurações do sistema carregadas do .env e validadas pelo Pydantic."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Iugu ---
    iugu_api_token: str = Field(..., description="Token da API da Iugu (Live ou Test)")
    iugu_api_base_url: str = Field("https://api.iugu.com", description="URL base da API")
    iugu_account_id: str = Field("", description="ID da conta Iugu")
    iugu_webhook_token: str = Field("", description="Token para validar webhooks")

    # --- Planilha ---
    planilha_empresas: Path = Field(
        PROJECT_ROOT / "empresas_autorizadas.xlsx",
        description="Caminho para a planilha de empresas autorizadas",
    )

    # --- Webhook Server ---
    webhook_host: str = Field("0.0.0.0", description="Host do servidor de webhook")
    webhook_port: int = Field(8000, description="Porta do servidor de webhook")
    webhook_log_level: str = Field("INFO", description="Nível de log")

    # --- Autenticação (API de gestão / app mobile) ---
    api_usuario: str = Field("admin", description="Usuário de login da API de gestão")
    api_senha: str = Field("", description="Senha de login da API de gestão")
    api_jwt_secret: str = Field("", description="Chave secreta para assinar tokens JWT")
    api_jwt_expira_horas: int = Field(72, description="Validade do token JWT em horas")
    # Origens permitidas no CORS (separadas por vírgula). Em produção, restrinja ao
    # domínio do painel. Use "*" só em desenvolvimento.
    cors_origins: str = Field(
        "https://iugu.megasuporte.com",
        description="Origens permitidas no CORS (separadas por vírgula)",
    )

    # --- NFS-e DF ---
    # ADR-0005: seletor de backend de emissão (abstração por protocolo).
    # "nacional"  = Padrão Nacional CGNFS-e (DPS v1.01) — comportamento atual, default.
    # "abrasf204" = ABRASF 2.04 (RPS série 3, ISSnet DF) — produção de transição (Parte B).
    # A virada de produção é trocar NFSE_PADRAO no .env, não reescrever código.
    # Default "nacional" para PRESERVAR o comportamento atual; produção setará
    # "abrasf204" quando a Parte B estiver pronta e validada.
    nfse_padrao: Literal["nacional", "abrasf204"] = Field(
        "nacional",
        description="Protocolo de emissão NFS-e: 'nacional' (DPS, atual) ou 'abrasf204' (RPS, transição)",
    )
    # URLs do webservice ABRASF 2.04 (ISSnet DF) — usadas pelo backend abrasf204 (Parte B).
    nfse_ws_url_abrasf_homologacao: str = Field(
        "https://www.issnetonline.com.br/homologaabrasf/webservicenfse204/nfse.asmx",
        description="URL do webservice ABRASF 2.04 (ISSnet) em homologação",
    )
    nfse_ws_url_abrasf_producao: str = Field(
        "https://df.issnetonline.com.br/webservicenfse204/nfse.asmx",
        description="URL do webservice ABRASF 2.04 (ISSnet) em produção",
    )
    # Série do RPS no DF (ABRASF 2.04) — fixa em "3" conforme habilitação do Nota Control.
    nfse_serie_rps: str = Field("3", description="Série do RPS no DF (ABRASF 2.04)")
    # Código CNAE do serviço (ABRASF 2.04) — obrigatório no ISSnet DF (erro L001 sem ele).
    # tsCodigoCnae = xsd:int totalDigits=7. 6209100 = Suporte técnico em TI.
    nfse_cnae: str = Field(
        "6209100", description="Código CNAE do serviço (6209100 = Suporte técnico em TI)"
    )
    # Município de incidência do ISSQN (ABRASF 2.04) — obrigatório quando ExigibilidadeISS=1
    # (erro E311 sem ele). tsCodigoMunicipioIbge = xsd:int totalDigits=7. Brasília = 5300108.
    nfse_municipio_incidencia: str = Field(
        "5300108", description="Código IBGE do município de incidência do ISSQN (Brasília=5300108)"
    )

    nfse_inscricao_municipal: str = Field("", description="Inscrição municipal DF")
    nfse_cnpj_prestador: str = Field("", description="CNPJ do prestador (só números)")
    nfse_razao_social_prestador: str = Field("", description="Razão social do prestador")
    nfse_certificado_path: Path = Field(
        PROJECT_ROOT / "certs" / "meu_certificado.pfx",
        description="Caminho do certificado .pfx",
    )
    nfse_certificado_senha: str = Field("", description="Senha do certificado A1")
    nfse_ambiente: Literal["homologacao", "producao"] = Field(
        "homologacao", description="Ambiente NFS-e DF"
    )
    nfse_codigo_servico_padrao: str = Field("010701", description="Código de serviço padrão (cTribNac: 010701 = Suporte técnico em informática)")
    nfse_descricao_servico_padrao: str = Field(
        "PRESTAÇÃO DE SERVIÇOS TÉCNICOS E ESPECIALIZADOS EM TI",
        description="Descrição padrão do serviço (validada pelo contador em 2026-04-20)"
    )
    nfse_aliquota_iss_padrao: float = Field(2.0, description="Alíquota ISS padrão (%)")
    # Código de tributação municipal do ISSQN (obrigatório no XSD v1.01)
    # Para o DF: 1071 = Suporte técnico em informática (validado pelo contador em 2026-04-20)
    nfse_codigo_trib_municipal: int = Field(
        1071, description="Código de tributação municipal do ISSQN (inteiro até 10 dígitos)"
    )
    # Código NBS (Nomenclatura Brasileira de Serviços) — opcional no XSD mas preenchido nas NFs reais
    # 115013000 = 1.1501.30.00 (Serviços de suporte em tecnologia da informação)
    nfse_nbs_padrao: str = Field(
        "115013000", description="Código NBS do serviço (9 dígitos, validado pelo contador)"
    )

    # URLs do webservice (preencher quando Nota Control enviar o manual)
    # Tanto SOAP quanto REST são suportados — o código detecta pelo content-type.
    nfse_ws_url_homologacao: str = Field(
        "", description="URL do webservice NFS-e DF em homologação"
    )
    nfse_ws_url_producao: str = Field(
        "", description="URL do webservice NFS-e DF em produção"
    )
    # "soap" ou "rest" — define o protocolo de envio. Padrão SOAP (ABRASF → CGNFS-e DF).
    nfse_ws_protocolo: Literal["soap", "rest"] = Field(
        "soap", description="Protocolo do webservice NFS-e DF"
    )
    # Modo dry-run: monta os dados da NFS-e e loga tudo, mas NÃO gera XML nem envia
    nfse_dry_run: bool = Field(
        False, description="Se True, apenas monta e loga os dados da NFS-e sem gerar/enviar"
    )
    # Pasta onde XMLs e PDFs emitidos são arquivados
    nfse_output_dir: Path = Field(
        PROJECT_ROOT / "nfse_emitidas",
        description="Diretório onde XMLs e PDFs das NFS-e emitidas são salvos",
    )
    # Código IBGE do município emissor (Brasília = 5300108)
    nfse_codigo_municipio_emissor: str = Field(
        "5300108", description="Código IBGE do município emissor (Brasília=5300108)"
    )
    # Série e numeração da DPS — início. Incrementa automaticamente.
    nfse_serie_padrao: str = Field("00001", description="Série padrão da DPS")

    # --- Regime tributário do prestador (B-40 regTrib — obrigatório no XSD) ---
    # opSimpNac: 1=Não Optante, 2=Optante MEI, 3=Optante ME/EPP (Simples Nacional)
    nfse_op_simples_nacional: int = Field(
        3, description="Situação perante Simples Nacional (1=Não Opt, 2=MEI, 3=ME/EPP)"
    )
    # regApTribSN (opcional): 1=SN total, 2=SN fed + ISS municipal, 3=tudo separado
    nfse_regime_apuracao_sn: int = Field(
        1, description="Regime de Apuração SN (1=SN total, 2=ISS separado)"
    )
    # regEspTrib: 0=Nenhum, 1=Coop, 2=Estimativa, 3=ME Municipal, 4=Notário, 5=Autônomo, 6=Soc Prof
    nfse_regime_especial_trib: int = Field(
        0, description="Regime Especial de Tributação Municipal (0=Nenhum)"
    )

    # --- Lei 12.741/2012 — total aproximado de tributos ---
    # Para Simples Nacional ME/EPP: usar o % efetivo do DAS ou valor IBPT
    # Valor validado pelo contador em 2026-04-20: 7.48%
    nfse_percentual_tributos_sn: float = Field(
        7.48, description="Percentual total aprox. dos tributos (Lei 12.741/2012, validado pelo contador)"
    )

    # --- Faturamento / Cobrança ---
    # Métodos de pagamento aceitos nas faturas (lista separada por vírgula)
    fatura_metodos_pagamento: str = Field(
        "bank_slip,pix",
        description="Métodos de pagamento: bank_slip, pix, credit_card, all",
    )
    # Dias após vencimento para a fatura expirar (0-120). 0 = expira no dia.
    fatura_dias_expiracao: int = Field(
        30, description="Dias após vencimento para expirar a fatura (0-120)",
    )
    # Prazo extra para pagamento do boleto após vencimento (1-120 dias)
    fatura_boleto_extra_due: int = Field(
        30, description="Dias extras para pagar boleto após vencimento (1-120)",
    )
    # Multa por atraso (percentual sobre valor total). Máximo legal: 2%.
    fatura_multa_atraso_percentual: float = Field(
        2.0, description="Multa % por atraso (máximo legal 2%)",
    )
    # Juros por dia de atraso. True = 1% ao mês pro rata (padrão Iugu).
    fatura_juros_por_dia: bool = Field(
        True, description="Cobrar juros por dia de atraso (1% ao mês pro rata)",
    )
    # Desabilitar e-mail de cobrança no vencimento (False = envia normalmente)
    fatura_ignorar_email_vencimento: bool = Field(
        False, description="Se True, não envia e-mail de cobrança no vencimento",
    )

    # --- E-mail (SMTP) para envio de NFS-e ---
    smtp_host: str = Field("", description="Servidor SMTP (ex: smtp.gmail.com)")
    smtp_port: int = Field(587, description="Porta SMTP (587=STARTTLS, 465=SSL)")
    smtp_usuario: str = Field("", description="Usuário/e-mail de autenticação SMTP")
    smtp_senha: str = Field("", description="Senha ou app password do SMTP")
    smtp_usar_tls: bool = Field(True, description="Usar STARTTLS (True) ou SSL direto (False)")
    smtp_remetente_nome: str = Field(
        "MEGASUPORTE TI", description="Nome exibido como remetente"
    )
    smtp_remetente_email: str = Field(
        "", description="E-mail do remetente (se vazio, usa smtp_usuario)"
    )

    # --- IBSCBS (Reforma Tributária EC 132/2023 — obrigatório no schema v1.01) ---
    # Código indicador de operação (6 dígitos) — tabela IndOp_IBSCBS.xlsx do Nota Control
    # Campo OPCIONAL segundo contador (validado em 2026-04-20) — deixar vazio
    nfse_ibscbs_cIndOp: str = Field(
        "", description="Código Indicador de Operação IBSCBS (6 dígitos, opcional)"
    )
    # CST — Código de Situação Tributária IBS/CBS (3 dígitos) — tabela cClassTribIBSCBS
    # 900 = Tributação pelo Simples Nacional (CST específico para optantes SN)
    nfse_ibscbs_cst: str = Field(
        "900", description="CST IBS/CBS (3 dígitos)"
    )
    # cClassTrib — Classificação Tributária IBS/CBS (6 dígitos)
    # 900001 = Simples Nacional — tributação unificada via DAS
    nfse_ibscbs_cClassTrib: str = Field(
        "900001", description="Classificação Tributária IBS/CBS (6 dígitos)"
    )


# Instância global reutilizada em todos os módulos
settings = Settings()


def get_nfse_endpoint() -> str:
    """Retorna o endpoint do webservice NFS-e DF conforme o ambiente configurado.

    Solicite a URL real ao Nota Control (suporte.df@notacontrol.com.br) e
    preencha NFSE_WS_URL_HOMOLOGACAO / NFSE_WS_URL_PRODUCAO no .env.
    """
    if settings.nfse_ambiente == "producao":
        return settings.nfse_ws_url_producao
    return settings.nfse_ws_url_homologacao
