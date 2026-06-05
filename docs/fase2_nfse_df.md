# Fase 2 — Emissão automática de NFS-e no DF (CGNFS-e)

Este documento explica o que foi implementado na Fase 2 e o que falta fazer para
colocar em produção.

> **Histórico:** documento original escrito em 19/04/2026 para o Padrão Nacional (DPS v1.01). O cabeçalho abaixo reflete o estado real em **05/06/2026**. As seções seguintes ainda descrevem o backend nacional (DPS) e seguem válidas como referência técnica desse caminho.

## ✅ Status atual (05/06/2026) — emissor dual pronto; aguardando validação do XML pelo Nota Control

A Fase 2 saiu do bloqueio cadastral e migrou para uma **arquitetura dual** que cobre o presente e o futuro:

- A MEGASUPORTE foi **habilitada em produção** pelo Nota Control (chamado de 05/06/2026). O webservice **oficial em produção é o ABRASF 2.04**, série RPS 3 — não o Padrão Nacional (DPS), que ainda dá 404. O Padrão Nacional só vira obrigatório em **30/06/2026** (prazo prorrogado).
- **Decisão arquitetural ADR-0005:** abstração por protocolo — dispatcher por `NFSE_PADRAO` no `.env` (`abrasf204` agora, `nacional` depois de 30/06). A virada vira **troca de uma variável**, não reescrita. Ver `docs/adr/ADR-0005-abrasf-2.04-rps.md` + `docs/adr/README.md`.
- **Backend nacional (DPS v1.01):** ✅ pronto, XML válido contra XSD v1.01, E160 resolvido (5 patches lxml + IBSCBS) — fica como `_emitir_nacional` em `src/nfse_df.py`.
- **Backend ABRASF 2.04 (RPS):** ✅ pronto estruturalmente em `src/nfse_df.py` (`_emitir_abrasf204`), XML válido contra o XSD oficial **com e sem Signature** (`scripts/validar_rps_xsd.py` e `--com-assinatura`). Dois fixes do code-reviewer + appsec aplicados (05/06/2026):
  - Reposicionamento da `Signature` para dentro de `<Rps>` (irmã de `InfDeclaracaoPrestacaoServico`) — antes ela caía como filha de `GerarNfseEnvio`, o que daria E160 no primeiro envio real.
  - Endurecimento do helper de PEMs do certificado A1 (`tempfile.mkstemp` + `chmod 0600` + warning no `finally`) e parser XML seguro anti-XXE em todo o parsing.
- **Fiscal:** código de tributação `1071` + alíquota `2%` **confirmados pela contabilidade em 05/06/2026** (a ficha cadastral lista 1071=5% genérico, mas o contador autorizou 1071+2% para este prestador). `cTribMun=1071`, `cNBS=115013000`, IBSCBS CST `900`/cClassTrib `900001` — sem mudança no `.env`.
- **CF/DF / IM:** `0796481500161` confirmado correto pela ficha cadastral oficial — o problema antigo era habilitação, não valor.

### Pendências para o primeiro envio real (não-bloqueios estruturais)

O backend está pronto, mas alguns detalhes do ISSnet DF só se confirmam batendo o servidor real. Estão centralizados como constantes no topo da seção ABRASF em `src/nfse_df.py` (qualquer ajuste é "1 linha"):

1. **Namespace de serviço exato** (`ABRASF_SERVICE_NS`, hoje `http://nfse.abrasf.org.br`)
2. **SOAPAction exato** (com/sem aspas)
3. **`nfseCabecMsg`/`nfseDadosMsg`** como XML aninhado vs CDATA (ramo CDATA pronto, comentado)
4. **`versaoDados`** aceito (`2.04`)
5. **Faixa inicial do RPS série 3** — precisa ser **solicitada no portal ISSnet** (menu "Solicitação de Documentos Fiscais"); o contador `.contador_rps.json` começa em 1 e deve ser alinhado antes do primeiro envio
6. Consistência `01.07` ↔ `1071` no envio real

### Próximo passo concreto

1. Gerar RPS de exemplo (`scripts/validar_rps_xsd.py --com-assinatura` em modo dry-run) e enviar para **`integracao.df@notacontrol.com.br`** pedindo validação do XML.
2. Solicitar a faixa de RPS série 3 no portal ISSnet.
3. Após retorno deles: ajustar constantes (se houver) → homologação ISSnet (`NFSE_PADRAO=abrasf204` + `NFSE_AMBIENTE=homologacao`) → 1 RPS produção R$1,00 com `--dry-run` antes do envio real.

---

> Histórico abaixo (escrito em abril/2026 sobre o caminho DPS) — preservado como referência técnica do backend nacional.

---

## Achados do Manual v1.01 (aplicados ao nosso código)

### Protocolo
- **SOAP 1.1**, não 1.2 (seção 7.3.1 do manual)
- Style/Encoding: **Document/Literal wrapped**
- `Content-Type: text/xml; charset=utf-8`
- `SOAPAction: "http://www.sped.fazenda.gov.br/nfse/GerarNfse"`
- Namespace único: **`http://www.sped.fazenda.gov.br/nfse`**

### Operação que usamos: `GerarNfse` (síncrona, 1 DPS)

O manual oferece 3 formas de enviar DPS:

| Operação | Tipo | Entrada | Saída | Uso |
|----------|------|---------|-------|-----|
| `RecepcionarLoteDps` | Assíncrona | Lote (N DPS) | Protocolo | Precisa de consulta depois |
| `RecepcionarLoteDpsSincrono` | Síncrona | Lote (N DPS) | Lista NFS-e | Múltiplas de uma vez |
| **`GerarNfse`** | **Síncrona** | **1 DPS** | **1 NFS-e** | **✅ Nossa escolha** |

Escolhemos `GerarNfse` porque nosso fluxo é "1 fatura paga Iugu → 1 NFS-e". Resposta imediata, sem necessidade de consulta posterior.

### Estrutura do envelope SOAP

```xml
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GerarNfse xmlns="http://www.sped.fazenda.gov.br/nfse">
      <nfseDadosMsg>
        <GerarNfseEnvio xmlns="http://www.sped.fazenda.gov.br/nfse">
          <DPS versao="1.00">
            <infDPS Id="DPS...">...</infDPS>
            <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">...</Signature>
          </DPS>
        </GerarNfseEnvio>
      </nfseDadosMsg>
    </GerarNfse>
  </soap:Body>
</soap:Envelope>
```

Já implementado em `_envelopar_soap()` no `src/nfse_df.py`.

### Estrutura da resposta (`GerarNfseResposta`)

**Sucesso:**
```
GerarNfseResposta
  └── ListaNfse
        └── CompNfse
              └── Nfse
                    └── infNFSe
                          ├── nNFSe          ← número aprovado
                          ├── cCodVerif      ← código verificação
                          └── dhProc
```

**Rejeição:**
```
GerarNfseResposta
  └── ListaMensagemRetorno
        └── MensagemRetorno
              ├── Codigo    (ex: "E425")
              ├── Mensagem  (ex: "Inscrição Municipal inválida")
              └── Correcao  (ex: "Verifique se a IM está ativa")
```

Já implementado em `_parsear_resposta()` no `src/nfse_df.py`.

### Assinatura digital (seção 7.3.3 do manual)

Confirmado que nosso código está correto:
- **xmldsig-core** (`http://www.w3.org/TR/xmldsig-core/`)
- `CanonicalizationMethod`: `http://www.w3.org/TR/2001/REC-xml-c14n-20010315`
- `SignatureMethod`: `http://www.w3.org/2000/09/xmldsig#rsa-sha1`
- `DigestMethod`: `http://www.w3.org/2000/09/xmldsig#sha1`
- **Certificado do tipo A** (A1 ou A3), **não S** — ✅ nosso A1 SERPRORFB é tipo A
- NÃO deve incluir as tags `X509SubjectName`, `X509IssuerSerial`, `KeyValue`, `RSAKeyValue`, etc. (são derivadas do certificado)
- DEVE incluir `X509Certificate` com o certificado em base64 (nosso código faz isso via `erpbrasil.assinatura`)

### Contato Nota Control (homologação)

- **Telefone:** (67) 3041-2075
- **E-mail:** suporte@notaeletronica.com.br
- **Horário:** segunda a sexta, 08h30 às 18h30 (horário de Brasília)
- **XSD oficial:** https://www.notacontrol.com.br/download/nfse/schema_v101.xsd

### Todas as 12 operações disponíveis (para referência futura)

| Operação | Síncrona? | Uso típico |
|----------|-----------|------------|
| `RecepcionarLoteDps` | Não | Lote grande com consulta posterior |
| `RecepcionarLoteDpsSincrono` | Sim | Lote pequeno com resposta imediata |
| `GerarNfse` ⭐ | Sim | 1 DPS → 1 NFS-e (usado por nós) |
| `CancelarNfse` | Sim | Cancela NFS-e já emitida |
| `ConsultarLoteDps` | Sim | Ver status de lote assíncrono |
| `ConsultarNfsePorDps` | Sim | Buscar NFS-e pelo número da DPS |
| `ConsultarNfseServicoPrestado` | Sim | Listar NFS-e emitidas |
| `ConsultarNfseServicoTomado` | Sim | Listar NFS-e recebidas |
| `ConsultarNfsePorFaixa` | Sim | Listar NFS-e num intervalo |
| `ConsultarDadosCadastrais` | Sim | Ver cadastro do prestador |
| `ConsultarDpsDisponivel` | Sim | Listar DPS que ainda cabem gerar NFS-e |
| `ConsultarUrlNfse` | Sim | Obter URL pública da NFS-e |

No futuro, o ideal é também implementarmos `CancelarNfse` e `ConsultarNfsePorDps` para completude.

---



## O que está pronto (100% implementado)

- ✅ **Montagem da DPS** (Declaração de Prestação de Serviços) em XML no padrão
  nacional CGNFS-e v1.00, usando a biblioteca oficial [`nfelib`](https://github.com/akretion/nfelib).
  O XML é gerado automaticamente a partir dos dados da fatura da Iugu + empresa
  da planilha, incluindo prestador, tomador, serviço, valores e tributação.
- ✅ **Assinatura digital** do XML com certificado A1 (`.pfx`) via
  [`erpbrasil.assinatura`](https://github.com/erpbrasil/erpbrasil.assinatura).
- ✅ **Envio ao webservice** com suporte a **SOAP e REST** (configurável via
  `NFSE_WS_PROTOCOLO`), incluindo autenticação mTLS (certificado cliente).
- ✅ **Parsing de resposta** flexível — detecta aprovação/rejeição,
  extrai número da NFS-e e código de verificação, coleta mensagens de erro.
- ✅ **Arquivamento automático** do XML enviado e do retorno em `nfse_emitidas/`.
- ✅ **Numeração automática** da DPS (contador persistido em arquivo).
- ✅ **Script `emitir_nfse_manual.py`** para testes e homologação sem depender
  do webhook.
- ✅ **Dry-run** que monta e assina a DPS sem enviar — útil para validar XML
  antes de ter as URLs do webservice.

## O que você precisa fazer antes de emitir a primeira nota

### 1. Obter as URLs do webservice com o Nota Control

Envie e-mail para **suporte.df@notacontrol.com.br** pedindo:

> Prezados, estou desenvolvendo uma integração para emissão automática de NFS-e
> no DF no padrão nacional CGNFS-e. Favor me enviar:
>
> 1. Manual de integração do webservice (padrão nacional)
> 2. URL do ambiente de homologação
> 3. URL do ambiente de produção
> 4. WSDL (se SOAP) ou especificação OpenAPI (se REST)
> 5. Liberação do meu CNPJ no ambiente de homologação
>
> Dados do prestador:
> - CNPJ: [seu CNPJ]
> - Inscrição Municipal: [sua IM]
> - Razão Social: MEGASUPORTE SERVICOS DE TI

### 2. Preencher o `.env` com os dados recebidos

Depois que receber resposta, adicione ao `.env`:

```
NFSE_INSCRICAO_MUNICIPAL=seu_numero_aqui
NFSE_CNPJ_PRESTADOR=36342291000143
NFSE_RAZAO_SOCIAL_PRESTADOR=MEGASUPORTE SERVICOS DE TI

NFSE_CERTIFICADO_PATH=./certs/meu_certificado.pfx
NFSE_CERTIFICADO_SENHA=sua_senha

NFSE_AMBIENTE=homologacao
NFSE_WS_URL_HOMOLOGACAO=https://hom.nfse.fazenda.df.gov.br/ws/...
NFSE_WS_URL_PRODUCAO=https://nfse.fazenda.df.gov.br/ws/...
NFSE_WS_PROTOCOLO=soap

NFSE_CODIGO_SERVICO_PADRAO=01.07
NFSE_ALIQUOTA_ISS_PADRAO=2.0
```

### 3. Colocar o certificado A1 em `/certs/`

```powershell
# No Windows
copy C:\caminho\do\seu\certificado.pfx .\certs\meu_certificado.pfx
```

### 4. Instalar as novas dependências

```powershell
cd "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

As novas dependências são: `nfelib`, `erpbrasil.assinatura`.

## Como testar

### Passo 1 — Validar configuração

```powershell
python scripts\test_connection.py
```

Deve aparecer ✅ em Planilha, Iugu e NFS-e DF.

### Passo 2 — Testar montagem e assinatura (sem enviar)

Gera uma DPS de exemplo com dados fictícios (sem precisar de invoice real):

```powershell
python scripts\emitir_nfse_manual.py --exemplo --dry-run
```

O script vai:
1. Montar a DPS com dados de exemplo
2. Tentar assinar com seu certificado A1
3. Salvar em `nfse_emitidas/dps_exemplo_XXXXX.xml`
4. Não envia — só prepara e salva o XML

Abra o XML gerado e verifique se está como esperado.

### Passo 3 — Homologação real

Depois que o Nota Control te liberar no ambiente de teste:

```powershell
# Pega um invoice_id de uma fatura paga da Iugu
curl.exe -u "SEU_TOKEN:" "https://api.iugu.com/v1/invoices?limit=1&status_filter=paid"

# Emite para essa fatura (ambiente homologação)
python scripts\emitir_nfse_manual.py <invoice_id>
```

Ou para forçar mesmo sem fatura paga (útil para testes):

```powershell
python scripts\emitir_nfse_manual.py <invoice_id> --forcar
```

A resposta terá:

```json
{
  "sucesso": true,
  "numero_nfse": "2026000000001",
  "codigo_verificacao": "ABC123DEF456",
  "xml_enviado_path": "nfse_emitidas/dps_000000000000001_enviada_20260419_120000.xml",
  "xml_retorno_path": "nfse_emitidas/dps_000000000000001_retorno_20260419_120005.xml"
}
```

### Passo 4 — Emissão automática via webhook

Quando uma fatura for paga e o CNPJ estiver na planilha com `emitir_nf=True`,
o servidor webhook automaticamente chama `emitir_nfse()`. Basta confirmar
em produção:

```
NFSE_AMBIENTE=producao
```

## Arquitetura final

```
┌──────────────┐   POST paid   ┌──────────────────────┐
│     IUGU     │ ────────────► │  FastAPI webhook     │
└──────────────┘               │  /webhook/iugu       │
                               └──────────┬───────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │  Planilha            │
                               │  empresas autorizadas│
                               └──────────┬───────────┘
                                          │ CNPJ autorizado +
                                          │ emitir_nf=True?
                                          ▼
              ┌───────────────────────────────────────────────┐
              │  src/nfse_df.py                               │
              │                                               │
              │  1. Montar DPS (nfelib)                       │
              │     prestador=.env  tomador=invoice+empresa   │
              │                                               │
              │  2. Assinar (erpbrasil.assinatura)            │
              │     usa .pfx + senha do .env                  │
              │                                               │
              │  3. Enviar (SOAP/REST, mTLS)                  │
              │     URL configurável por ambiente             │
              │                                               │
              │  4. Parsear retorno (lxml)                    │
              │     número NFS-e + código verificação         │
              │                                               │
              │  5. Arquivar em nfse_emitidas/                │
              └───────────────────────────────────────────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │  ISS DF Webservice   │
                               │ (Nota Control/ISSnet)│
                               └──────────────────────┘
```

## Troubleshooting

| Sintoma | Causa provável | Solução |
|---------|---------------|---------|
| `Dependências ausentes` | `nfelib` não instalada | `pip install -r requirements.txt` |
| `Certificado não encontrado` | `.pfx` não está em `/certs/` | Copie o arquivo e ajuste `NFSE_CERTIFICADO_PATH` |
| `Senha incorreta ao abrir .pfx` | `NFSE_CERTIFICADO_SENHA` errada | Confira a senha do certificado |
| `URL do webservice não configurada` | `.env` sem a URL | Pedir ao Nota Control e preencher |
| HTTP 500 do webservice | Formato SOAP incorreto | Verifique `soapAction` no `_envelopar_soap` conforme manual |
| HTTP 401/403 mTLS | Certificado não aceito | Ambiente pode exigir liberação prévia — contatar Nota Control |
| `cStat=400` com "CNPJ inválido" | Prestador não homologado | Validar dados no `.env` vs cadastro ISS DF |
| Retorno sem `nNFSe` | Estrutura de retorno específica do DF | Inspecionar XML em `nfse_emitidas/` e ajustar `_parsear_resposta` |

## Pontos que podem precisar de ajuste após o primeiro envio

Esses detalhes dependem do **manual específico do DF** que o Nota Control vai
te enviar. Estão implementados de forma razoável, mas podem precisar de
adaptação:

1. **`soapAction` e nome da operação SOAP** (`_envelopar_soap` em `nfse_df.py`)
   — usamos `RecepcionarLoteDps` como padrão. Se o DF usar outro nome, mude lá.

2. **Namespace XML do serviço SOAP** — usamos
   `http://www.sped.fazenda.gov.br/nfse`. Pode variar.

3. **Estrutura de resposta** (`_parsear_resposta`) — buscamos os elementos
   `nNFSe`, `cCodVerif`, `cStat` etc. por *localname*, tolerante a namespaces.
   Mas se o retorno tiver estrutura muito diferente (ex: wrapper custom do
   Nota Control), ajuste lá.

4. **Código IBGE por cidade** — mapeamos só algumas cidades principais. Se um
   tomador for de outra cidade, a função `_codigo_ibge_por_cidade` retornará
   o código do prestador. Expanda o dict `atalhos` ou integre uma API IBGE.

5. **Regime tributário do prestador** — hoje vai vazio no XML. Preencher
   `regTrib` em `_montar_xml_dps` conforme seu regime
   (Simples Nacional, MEI, Lucro Real etc.) — isso é mandatório em produção.

## Arquivos tocados nessa Fase 2

- `src/nfse_df.py` — **reescrito** (montagem + assinatura + envio + parsing + arquivamento)
- `src/config.py` — novas variáveis `NFSE_WS_URL_*`, `NFSE_WS_PROTOCOLO`, etc.
- `requirements.txt` — adicionadas `nfelib` e `erpbrasil.assinatura`
- `scripts/emitir_nfse_manual.py` — **novo** para homologação
- `docs/fase2_nfse_df.md` — este documento
