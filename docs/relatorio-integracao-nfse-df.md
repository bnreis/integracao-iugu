# Integração técnica para emissão de NFS-e do Distrito Federal (SEF-DF / ISSnet → Padrão Nacional)

## TL;DR
- O portal `iss.fazenda.df.gov.br` é um sistema **ASP.NET WebForms** operado pela empresa **Nota Control (sistema ISSnet/ISS.Net)** sob contrato da Secretaria de Economia do DF; existe **webservice oficial SOAP no padrão ABRASF 2.04** (produção: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`), e este é o caminho legítimo de integração — **não** automatize o portal web.
- O DF está **migrando para o Sistema Nacional NFS-e (padrão DPS, JSON + XML)**: o Modelo ABRASF foi formalmente substituído em 1º/1/2026, mas a Secretaria de Economia (Seec-DF) **prorrogou até 30/6/2026** o prazo de adequação dos integradores via webservice. O DF optou por manter o emissor próprio (ISSnet) compartilhando os documentos com o Ambiente Nacional de Dados (ADN).
- **Recomendação:** para entrar em produção hoje, integre o **ISSnet ABRASF 2.04** com certificado ICP-Brasil A1 e assinatura XML *enveloped*; em paralelo, **construa já para o Padrão Nacional (DPS)**, que é o destino obrigatório. Para acelerar com pouco esforço, considere um agregador (Nuvem Fiscal ou Focus NFe — ambos suportam Brasília/DF, IBGE 5300108, e o padrão nacional).

## Key Findings

1. **Provedor e tecnologia.** O Sistema de Gestão do ISS do DF é operado pela **Nota Control**, sob a marca técnica **ISSnet/ISS.Net**. O portal de login (`Login.aspx`) é ASP.NET WebForms (extensão `.aspx`), o que implica gestão de `ViewState`, `EventValidation` e sessão por cookie — características que tornam a automação de UI frágil. Porém, **não é necessário automatizar o portal**, pois há webservice SOAP oficial. Contatos de suporte de integração: tel. (67) 3041-2075, e-mail `suporte@notaeletronica.com.br`, chat `https://www.notaeletronica.com.br/painel/`.

2. **Webservice ABRASF 2.04 (modelo legado, ainda operante na transição).**
   - Homologação: `https://www.issnetonline.com.br/homologaabrasf/webservicenfse204/nfse.asmx`
   - Produção: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`
   - Padrão: **ABRASF versão 2.04**. Operações típicas do padrão: `RecepcionarLoteRps`, `RecepcionarLoteRpsSincrono`, `ConsultarLoteRps`, `ConsultarSituacaoLoteRps`, `ConsultarNfsePorRps`, `ConsultarNfse`/`ConsultarNfseServicoPrestado`, `CancelarNfse`, `GerarNfse`, `SubstituirNfse`.
   - Acesso: o prestador precisa solicitar liberação por e-mail e gerar a numeração de RPS no portal (menu `Solicitação de Documentos Fiscais > Solicitação`); o prazo de aprovação informado por integradores é de até 48h. A série de NFS-e indicada em materiais de integradores para o DF é "3".

3. **Certificado e assinatura.** Exige certificado **ICP-Brasil** (A1 recomendado para automação; arquivo PKCS#12 `.pfx`; A3 não funciona em fluxos automatizados sem hardware). A comunicação é HTTPS com TLS mútuo (mTLS). A assinatura é **XML Digital Signature (xmldsig), formato *enveloped***, com `CanonicalizationMethod` C14N (`http://www.w3.org/TR/2001/REC-xml-c14n-20010315`), `SignatureMethod` RSA-SHA1 e `DigestMethod` SHA1. No ABRASF 2.04 assina-se tanto o RPS individual (`InfDeclaracaoPrestacaoServico`/`InfRps`, identificado por atributo `Id` referenciado em `Reference URI="#..."`) quanto o lote.

4. **Sistema Nacional NFS-e (destino obrigatório).** O DF aderiu ao modelo nacional via compartilhamento com o ADN, mantendo emissor próprio. A API nacional é **REST/JSON**, com o documento fiscal em **XML assinado, comprimido em GZip e codificado em Base64**, autenticação por **mTLS com certificado ICP-Brasil A1/A3**. Conceito central: o contribuinte (prestador) envia uma **DPS (Declaração de Prestação de Serviços)** e recebe a **NFS-e** com chave de acesso de 50 caracteres.
   - **SEFIN (emissão):** Produção Restrita `https://sefin.producaorestrita.nfse.gov.br` / Produção `https://sefin.nfse.gov.br`; rota de emissão síncrona `POST {base}/SefinNacional/nfse`; consulta `GET {base}/SefinNacional/nfse/{chaveAcesso}`.
   - **ADN (distribuição de DF-e por NSU, eventos, parâmetros municipais, DANFSe):** Produção Restrita `https://adn.producaorestrita.nfse.gov.br` / Produção `https://adn.nfse.gov.br`.
   - Documentação oficial: `https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica` e o "Manual do Contribuinte – Emissor Público API" (v1.2, out/2025). As APIs de Produção Restrita e Produção foram liberadas oficialmente em 1º/10/2025.

5. **Cronograma DF e contexto regulatório.**
   - O **Modelo ABRASF** (criado pela ABRASF em 2005) **não recebe mais atualizações**: com a Lei Complementar nº 214/2025, o padrão nacional passa a ser de adoção obrigatória por todos os municípios a partir de 1º de janeiro de 2026.
   - Cronograma do DF (Nota Control/ISSnet): manuais e XSD do padrão nacional na aba Downloads até 7/11/2025; webservice de homologação até 17/11/2025; desativação formal do ABRASF e ativação do padrão nacional em produção em 1º/1/2026.
   - **Prorrogação:** o secretário-executivo da Receita da Seec-DF, **Clidiomar Soares**, estendeu o prazo até **30/6/2026** para que empresas, desenvolvedores e contadores concluam os ajustes com mais segurança.
   - **Emissor próprio:** comunicado da Seec-DF (30/12/2025) afirma que a Secretaria optou por manter o emissor próprio da NFS-e por meio do sistema ISSnet, e que o sistema fará o "De-Para" dos campos em conformidade com as Notas Técnicas.
   - **Grupos IBS/CBS:** os campos IBS/CBS só passam a ser obrigatórios a partir de 1º/8/2026 (Ato Conjunto RFB/CGIBS nº 1/2025, DOU de 23/12/2025; Resolução CGIBS nº 6 de 30/4/2026). O GDF sinaliza 2027 para o destaque pleno dos tributos.
   - **MEI:** microempreendedores individuais continuam emitindo pelo Portal Nacional, sem adaptação (adesão iniciada em 1º/9/2023, Resolução CGSN nº 172/2023).
   - **Simples Nacional:** Resolução CGSN nº 189, de 23/4/2026 (altera a Resolução CGSN nº 140/2018), torna obrigatória a emissão exclusivamente pelo Emissor Nacional (web ou API) para ME e EPP do Simples a partir de **1º/9/2026**.

## Details

### A) Análise do portal e padrões técnicos
O `iss.fazenda.df.gov.br/online/Login/Login.aspx` é ASP.NET WebForms. Para integração programática, ignore o portal HTML e use o webservice SOAP do ISSnet. O manual técnico atual da Nota Control (versão 1.01, revisada em 17/3/2026) descreve a estrutura de dados, métodos síncronos/assíncronos e regras de validação (cálculo de ISSQN, exigibilidade suspensa, regime especial de tributação, retenção). A descrição completa dos métodos ABRASF deve ser obtida no Manual de Integração ABRASF e nos XSD do ISSnet.

### B) Fluxo de emissão como PRESTADOR (ABRASF 2.04)
1. Solicitar liberação de acesso ao webservice e a numeração de RPS no portal.
2. Gerar o **RPS** (Recibo Provisório de Serviços) — documento de posse e responsabilidade do contribuinte, com numeração sequencial crescente, a ser convertido em NFS-e no prazo legal.
3. Montar o XML conforme `nfse.xsd` (ex.: `EnviarLoteRpsEnvio` / `GerarNfseEnvio`); usar UTF-8, ponto decimal, alíquotas em número inteiro percentual.
4. **Assinar** o RPS e o lote (xmldsig *enveloped*, C14N, RSA-SHA1, SHA1).
5. Enviar via SOAP (`RecepcionarLoteRps` assíncrono ou `RecepcionarLoteRpsSincrono`/`GerarNfse` síncrono). Em lote, um único erro invalida o lote inteiro.
6. Em modo assíncrono, guardar o protocolo e consultar com `ConsultarSituacaoLoteRps`/`ConsultarLoteRps`.
7. Obter a NFS-e (XML + link/PDF) por `ConsultarNfsePorRps`.
8. Cancelamento via `CancelarNfse`; substituição via `SubstituirNfse`.

Boa prática de homologação no DF: emitir ~5 RPS de teste no ambiente de homologação, consultar o recibo e, após aprovação, solicitar liberação de produção via e-mail. **Atenção:** os dados de teste padrão (CNPJ/IM fictícios) não devem ser usados em produção.

### C) Fluxo no Padrão Nacional (DPS)
O prestador (emitente) monta a DPS (XML), assina digitalmente, comprime em GZip, codifica em Base64 e faz `POST /SefinNacional/nfse`. Em modo síncrono, recebe a NFS-e gerada e a chave de acesso, ou rejeição (em falha de comunicação com o ADN, o documento entra em fila assíncrona). O cálculo de IBS/CBS é centralizado na Calculadora de Tributos da RTC, reduzindo a lógica tributária no ERP. Recuperação de documentos por NSU e eventos (cancelamento, manifestação, substituição) via ADN/API de Eventos. Idempotência recomendada: deduplicar por `idDps` + CNPJ + série + número.

### D) Projetos no GitHub

**Específicos para ISSnet (provedor do DF):**

| Projeto | Linguagem | Suporta DF? | Estrelas | Manutenção/Licença |
|---|---|---|---|---|
| **erpbrasil/nfselib.issnet** | Python | Sim (provedor ISSnet; schema v1_00) | ~3 | MIT; dormente (1 tag v0.1.0); não rotula explicitamente "ABRASF 2.04" |
| **akretion/nfselib** | Python | **Sim — lista Brasília (IBGE 5300108, DF) no grupo ISSNET** (Schema OK, SOAP OK) | ~19 | LGPL-2.1; 129 commits; renomeado `nfselib-legacy` no PyPI |
| **ctoigo/sped-nfse-issnet** | PHP | Padrão ISS.NET, mas Brasília não listada | ~2 | README incompleto — não usar |
| **robmachado/sped-nfse** | PHP | ISSNET em beta; Brasília não listada | ~21 (77 forks) | Marcado "em desenvolvimento, não usável" |

A `erpbrasil/nfselib.issnet` é a opção Python mais diretamente alinhada ao provedor do DF, mas é apenas geradora/leitora de XML (não faz a comunicação SOAP completa) e está pouco mantida. A `akretion/nfselib` é a única que mapeia explicitamente Brasília ao provedor ISSNET.

**Padrão Nacional (DPS):**

| Projeto | Linguagem | Foco | Estrelas | Observação |
|---|---|---|---|---|
| **kalmonv/node-sped-nfse** | TypeScript/Node | NFS-e Nacional/DPS | ~7 | Implementa DPS, DANFSE, consultas e eventos (schema v100); opção open-source mais direta para o nacional em Node |
| **nfewizard-org/nfewizard-io** | TypeScript/Node | NF-e/NFC-e/NFS-e/CT-e | ~180 | GPL-3.0; módulo `@nfewizard/nfse` "em fase de testes", testado apenas para SP |
| **nfephp-org/sped-nfse** / **nfelib** | PHP / Python | ABRASF + nacional | — | Frameworks de referência; `nfelib` traz bindings da NFS-e nacional em Python |

**Genéricos/de referência:**
- **TadaSoftware/PyNFe** (Python) — ~547 estrelas, release 0.6.5 (5/2/2026), o mais ativo da lista; **porém a NFS-e suporta apenas autorizadores Ginfes e Betha**, não ISSnet nem o padrão nacional — logo, **não atende o DF diretamente**.
- **OpenAC-Net/OpenAC.Net.NFSe** (C#) e **Projeto ACBr (ACBrNFSeX)** (Delphi/C#) — suportam o provedor ISSnet para Brasília/DF (5300108) e têm comunidade ativa discutindo o ISSnet DF e o RTC/padrão nacional; são referências fortes fora de Python/Node.

**Provedores comerciais (SDK/API) que abstraem o DF:**
- **Nuvem Fiscal** (`dev.nuvemfiscal.com.br`) — lista **Brasília (DF)** como cidade atendida; suporta NFS-e Nacional; API REST (endpoint `/nfse/dps`), SDK .NET oficial, autenticação por token OAuth (scope `nfse`).
- **Focus NFe** (`focusnfe.com.br`) — guia dedicado "emitir NFS-e em Brasília (DF)", usa `"codigo_municipio": 5300108`, inscrição correspondente ao **CF/DF**, série 8 em homologação; suporta NFS-e Nacional (REST/JSON, webhooks).
- Outros: PlugNotas/TecnoSpeed, WebmaniaBR, eNotas, NFe.io, Nota Gateway.

### E) Exemplos de código

**Python — assinatura XML (signxml) + cliente SOAP (zeep) para o ISSnet DF:**
```python
from signxml import XMLSigner, methods
from lxml import etree
import zeep
from requests import Session
from zeep.transports import Transport

# 1) Assinatura enveloped do RPS (InfDeclaracaoPrestacaoServico/InfRps com atributo Id)
root = etree.fromstring(xml_rps_bytes)
signed = XMLSigner(
    method=methods.enveloped,
    signature_algorithm="rsa-sha1",
    digest_algorithm="sha1",
    c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
).sign(root, key=key_pem, cert=cert_pem, reference_uri="#1")

# 2) mTLS + chamada SOAP (certificado extraído do .pfx ICP-Brasil)
session = Session()
session.cert = ("cert.pem", "key.pem")
client = zeep.Client(
    "https://df.issnetonline.com.br/webservicenfse204/nfse.asmx?wsdl",
    transport=Transport(session=session),
)
retorno = client.service.RecepcionarLoteRpsSincrono(etree.tostring(signed))
```
> Dica: valide o XML assinado no validador da Receita (`https://servicos.receita.fazenda.gov.br/servicos/assinadoc/ValidadorAssinaturas.app/valida.aspx`) — se reprovar lá, será rejeitado pelo ISSnet.

**Node.js/TypeScript — assinatura (xml-crypto) + mTLS (axios) para o ISSnet DF:**
```typescript
import { SignedXml } from "xml-crypto";
import * as https from "https";
import axios from "axios";

const sig = new SignedXml();
sig.signingKey = privateKeyPem;            // extraído do .pfx
sig.addReference(
  "//*[local-name(.)='InfDeclaracaoPrestacaoServico']",
  ["http://www.w3.org/2000/09/xmldsig#enveloped-signature",
   "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"],
  "http://www.w3.org/2000/09/xmldsig#sha1"
);
sig.computeSignature(xmlRps);

const agent = new https.Agent({ pfx: pfxBuffer, passphrase: senha });
await axios.post(
  "https://df.issnetonline.com.br/webservicenfse204/nfse.asmx",
  soapEnvelope,
  { httpsAgent: agent,
    headers: { "Content-Type": "text/xml; charset=utf-8", SOAPAction: "...RecepcionarLoteRpsSincrono" } }
);
```

**Padrão Nacional (DPS) — Node, conceito REST com mTLS:**
```typescript
// XML da DPS assinado (xmldsig), depois GZip + Base64, enviado em JSON ao SEFIN nacional
const agent = new https.Agent({ pfx, passphrase });
const resp = await axios.post(
  "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse",
  { dpsXmlGZipB64 },                       // payload conforme leiaute oficial
  { httpsAgent: agent, headers: { "Content-Type": "application/json" } }
);
// resp -> NFS-e gerada + chaveAcesso (50 chars); consulta: GET /SefinNacional/nfse/{chaveAcesso}
```

Bibliotecas recomendadas: **assinatura XML** — `signxml` (Python), `xml-crypto` (Node); **cliente SOAP** — `zeep` (Python), `soap`/`strong-soap` (Node); **REST** — `requests`/`httpx` (Python), `axios` (Node), sempre com `pfx`/mTLS configurado.

### F) Alternativa de automação do portal (NÃO recomendada)
Caso o webservice estivesse indisponível, seria possível automatizar via Selenium/Playwright: login (certificado ou CPF/senha), captura e reenvio de `__VIEWSTATE`/`__EVENTVALIDATION` a cada postback, preenchimento do formulário de emissão e download do PDF/XML. **Riscos:** quebra a cada atualização de layout; instabilidade de ViewState/EventValidation; dificuldade de usar certificado em navegador headless; ausência de garantia transacional e de protocolo formal; possível violação de termos de uso; e baixa escalabilidade. Use apenas como último recurso — **e nunca quando há webservice oficial, que é exatamente o caso do DF.**

## Recommendations
1. **Curto prazo (produção imediata):** integrar o **ISSnet ABRASF 2.04** (`df.issnetonline.com.br/webservicenfse204/nfse.asmx`), com certificado A1, assinatura xmldsig *enveloped*, homologando os RPS de teste e solicitando liberação de produção via `suporte@notaeletronica.com.br`. Base de código: `erpbrasil/nfselib.issnet` ou `akretion/nfselib` (Python), ou ACBrNFSeX/OpenAC (C#).
2. **Médio prazo (obrigatório):** desenvolver para o **Padrão Nacional (DPS)** contra `sefin.producaorestrita.nfse.gov.br` **antes de 30/6/2026**, validando o ciclo JSON + XML assinado + GZip + Base64 + mTLS. Base: `kalmonv/node-sped-nfse` (Node) ou `nfelib` (Python).
3. **Alternativa de baixo esforço:** se a equipe é pequena, use **Nuvem Fiscal** ou **Focus NFe**, que já abstraem o DF e o nacional, eliminando manutenção de XSD, assinatura e endpoints. Custo recorrente em troca de velocidade e menor risco regulatório.
4. **Gatilhos de decisão (benchmarks):**
   - Se a Seec-DF descontinuar definitivamente o ABRASF após 30/6/2026 → migre 100% para o nacional.
   - Quando os grupos IBS/CBS se tornarem obrigatórios (1º/8/2026) → garanta os campos IBSCBS na DPS.
   - Se você atende optantes do Simples → planeje o corte para o Emissor Nacional em 1º/9/2026.
   - Se o volume de notas for baixo/médio e a equipe de TI for enxuta → escolha agregador em vez de integração direta.

## Referências (links consolidados para o próximo agente)

**1. Webservice atual do DF — ISSnet ABRASF 2.04 (produção imediata)**
- Portal NFS-e DF (login + aba Downloads: manual técnico, XSD, comunicados de transição): https://iss.fazenda.df.gov.br/online
- Endpoint produção: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`
- WSDL produção: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx?wsdl`
- Endpoint homologação: `https://www.issnetonline.com.br/homologaabrasf/webservicenfse204/nfse.asmx`
- Painel/suporte de integração Nota Control: https://www.notaeletronica.com.br/painel/ (e-mail `suporte@notaeletronica.com.br`)

**2. Padrão Nacional NFS-e — DPS (destino obrigatório)**
- Documentação técnica oficial (manuais, leiautes, schemas XSD): https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica
- APIs de Produção Restrita e Produção (endpoints SEFIN/ADN, Swagger): https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao
- SEFIN emissão — Produção Restrita: `https://sefin.producaorestrita.nfse.gov.br` | Produção: `https://sefin.nfse.gov.br`
- ADN distribuição/eventos — Produção Restrita: `https://adn.producaorestrita.nfse.gov.br` | Produção: `https://adn.nfse.gov.br`

**3. Validação de assinatura digital (usar antes de enviar qualquer XML)**
- Validador de assinaturas da Receita Federal: https://servicos.receita.fazenda.gov.br/servicos/assinadoc/ValidadorAssinaturas.app/valida.aspx

**4. Código de referência (GitHub)**
- akretion/nfselib (Python — mapeia Brasília 5300108 ao provedor ISSnet): https://github.com/akretion/nfselib
- erpbrasil/nfselib.issnet (Python — gerador/leitor de XML ISSnet): https://github.com/erpbrasil/nfselib.issnet
- kalmonv/node-sped-nfse (TypeScript — DPS/NFS-e Nacional): https://github.com/kalmonv/node-sped-nfse
- nfewizard-org/nfewizard-io (TypeScript — multi-documento, módulo NFS-e em testes): https://github.com/nfewizard-org/nfewizard-io
- TadaSoftware/PyNFe (Python — referência ativa; NFS-e só Ginfes/Betha, não cobre DF): https://github.com/TadaSoftware/PyNFe

**5. Agregadores comerciais (caminho de baixo esforço)**
- Focus NFe — guia Brasília/DF (campos: código 5300108, CF/DF, série): https://focusnfe.com.br/guides/nfse/municipios-integrados/brasilia-df/
- Nuvem Fiscal — docs NFS-e: https://dev.nuvemfiscal.com.br/docs/nfse/

**6. Contexto regulatório e técnico (apoio)**
- Projeto ACBr — fórum ISSnet ABRASF 2.04 Brasília/DF (troubleshooting prático): https://www.projetoacbr.com.br/forum/topic/70308-nfse-issnet-abrasf-204-brasilia-df/

## Caveats
- As datas de transição mudaram repetidamente (1º/1/2026 → prorrogação para 30/6/2026); **confirme sempre** os comunicados em `iss.fazenda.df.gov.br/online` (aba Downloads) e o status real do ambiente de produção nacional do DF antes de cortar produção.
- Algumas sub-rotas/Swaggers da API nacional foram corroborados em fóruns de desenvolvedores; a fonte autoritativa é a página de APIs do `gov.br/nfse` (Produção Restrita e Produção) e o Manual do Contribuinte v1.2 (out/2025).
- As bibliotecas open-source específicas de ISSnet são pouco mantidas (poucas estrelas, releases antigos); valide a aderência ao layout 2.04 do DF antes de produção e tenha capacidade interna para corrigir o XML/assinatura.
- O DF **não possui "inscrição municipal" tradicional**: usa o **CF/DF (Cadastro Fiscal do Distrito Federal)** no lugar da inscrição municipal — atenção ao mapear esse campo em qualquer biblioteca/API.
- Mensagens de rejeição comuns no ISSnet DF incluem o erro **E160 "Arquivo em desacordo com o XML Schema"** — quase sempre ligado a divergência de versão de schema, encoding/acentuação ou assinatura inválida.
