# HANDOFF — Estado atual do projeto

> **Para o próximo assistente:** ponto de entrada da próxima sessão. Leia na íntegra antes de responder ao Bruno. Atualizado em **05/06/2026**.

**Usuário:** Bruno Reis (bruno.reis@grupontsec.com) — admin da conta Iugu da **MEGASUPORTE SERVIÇOS DE TI LTDA** (CNPJ 36.342.291/0001-43, Brasília/DF, Simples Nacional ME/EPP, ambiente "Estabelecido").

---

## 1. Projeto em uma linha

Automação que, ao receber webhook de fatura paga da Iugu, emite NFS-e automática no DF, gera boletos recorrentes mensais e tem app/painel de gestão. Hospedado em VPS própria da Hostinger (`iugu.megasuporte.com`, IP `72.62.11.230`).

## 2. Estado atual resumido (05/06/2026)

| Fase | Status | Observação |
|------|--------|------------|
| **Fase 1 — Webhook + cobrança + cadastro Iugu** | ✅ **Validada em produção** | Fluxo "fatura → pago → webhook → atualização" rodando no ar |
| **Fase 2 — Emissão NFS-e DF** | 🟡 **Estruturalmente pronta — aguardando validação do XML pelo Nota Control** | Habilitação em produção concluída. Próximo passo é mandar 1 RPS de exemplo para `integracao.df@notacontrol.com.br`. NFS-e real ainda não emitida |
| **Fase 3 — Deploy VPS** | ✅ **Concluída** | Backend, painel web, HTTPS, cron e hardening rodando |
| **Hardening de segurança (OWASP/pentest)** | ✅ Aplicado | CORS restrito, JWT, rate-limit, HSTS, compare_digest, /docs off etc. Detalhes em `docs/pentest_2026-06.md` e `docs/ressalvas_pentest_2026-06.md` |
| **Arquitetura interna (roadmap)** | 🟡 **Onda 0 + ADR-0003 Etapa 1 deployados** | ADR-0001 (SQLite), ADR-0002 (idempotência), ADR-0003 Etapa 2 e ADR-0004 propostos em `docs/adr/` |

## 3. Última conquista — Fase 2 destravada (não significa "emitiu nota")

A MEGASUPORTE foi **habilitada em produção** pelo Nota Control (chamado resolvido 05/06/2026). Junto vieram dois esclarecimentos que mudaram o rumo técnico:

1. **Produção do DF hoje é ABRASF 2.04, não Padrão Nacional.** Endpoint oficial: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`. Documento = RPS série 3.
2. **O Padrão Nacional (DPS v1.01) que já tínhamos pronto** só vira obrigatório em **30/06/2026** (prazo prorrogado). Por isso vai bem em XSD mas dava 404 no endpoint `wsnfsenacional` — não estava liberado lá.

Para cobrir os dois mundos, foi criado o **ADR-0005**: arquitetura dual (dispatcher por `NFSE_PADRAO` no `.env`). A virada de 30/06 vira "trocar 1 variável", não reescrita.

### Backends de emissão atuais (em `src/nfse_df.py`)

- **`_emitir_nacional`** (DPS v1.01) — pronto, XSD válido, E160 resolvido (5 patches lxml). Caminho do Padrão Nacional para depois de 30/06.
- **`_emitir_abrasf204`** (RPS série 3) — pronto estruturalmente, XSD válido **com e sem assinatura** (`scripts/validar_rps_xsd.py`/`--com-assinatura`). Caminho de produção HOJE.
- **`emitir_nfse(invoice, empresa)`** — dispatcher fino lê `settings.nfse_padrao` (`"nacional"` ou `"abrasf204"`).

### Dados fiscais confirmados (não pedir de novo)

- **CF/DF / IM:** `0796481500161` — bate com a ficha cadastral oficial.
- **Código de tributação municipal:** `1071` + alíquota **2%** — **confirmado pela contabilidade em 05/06/2026** (a ficha lista 1071=5% genérico, mas o contador autorizou 1071+2% para este prestador).
- `cTribNac=010701`, `cTribMun=1071`, `cNBS=115013000`, IBSCBS CST `900`/cClassTrib `900001`, tributos SN `7,48%`.

### Pendências do primeiro envio real (não-bloqueios estruturais)

Estão centralizadas como constantes no topo da seção ABRASF de `src/nfse_df.py` — qualquer ajuste depois do retorno do Nota Control é "1 linha":

1. Namespace de serviço exato do ISSnet (`ABRASF_SERVICE_NS`)
2. SOAPAction (com/sem aspas)
3. `nfseCabecMsg`/`nfseDadosMsg` aninhado vs CDATA (ramo CDATA pronto, comentado)
4. `versaoDados=2.04` aceito
5. **Faixa inicial do RPS série 3** — solicitar no portal ISSnet (menu "Solicitação de Documentos Fiscais") e ajustar o `.contador_rps.json` antes do primeiro envio
6. Consistência `01.07` ↔ `1071` no envio real

## 4. Próximo passo concreto

1. Rodar `scripts/validar_rps_xsd.py --com-assinatura` localmente para gerar o RPS assinado de exemplo.
2. Enviar o XML para **`integracao.df@notacontrol.com.br`** pedindo validação + esclarecer pendências 1-4 acima. Rascunho de e-mail pronto em `docs/email_notacontrol_padrao_nacional.md`.
3. Solicitar a **faixa de RPS série 3** no portal ISSnet.
4. Quando validarem: ajustar `.env` (`NFSE_PADRAO=abrasf204`, `NFSE_AMBIENTE=homologacao`) → homologar ISSnet → 1 RPS produção R$1,00 (com `--dry-run` antes).

## 5. Documentos a ler antes de mexer em código (ordem)

1. `CLAUDE.md` (este projeto) — visão estável.
2. **`docs/fase2_nfse_df.md`** — status técnico atualizado da Fase 2.
3. **`docs/adr/README.md` + `docs/adr/ADR-0005-abrasf-2.04-rps.md`** — decisão da arquitetura dual.
4. `docs/deploy_vps.md` — runbook vivo da infra (Fase 3 + hardening).
5. `docs/pentest_2026-06.md` + `docs/ressalvas_pentest_2026-06.md` — segurança.
6. `docs/relatorio-integracao-nfse-df.md` — pesquisa de contexto (ABRASF vs Padrão Nacional, libs, agregadores).

> `README.md` continua **desatualizado** — ignore.

## 6. Referências oficiais já no projeto

### Padrão Nacional (DPS v1.01)
- `docs/manual_oficial_integracao/Manual_integracao_v101.pdf` (109 páginas, 17/03/2026)
- `docs/exemplos_oficiais/GerarNfseEnvio.xml` (template comentado, 1.077 linhas)
- `docs/exemplos_oficiais/schema_v101.xsd.xml` (XSD oficial, 5.140 linhas)

### ABRASF 2.04 (RPS)
- `docs/exemplos_oficiais/abrasf204/schema/schema nfse v2-04.xsd` — XSD oficial ABRASF
- `docs/exemplos_oficiais/abrasf204/wsdl/nfse.wsdl` — WSDL oficial (operações, namespace, SOAPAction)
- `docs/exemplos_oficiais/abrasf204/manual_2.04.pdf` — Manual ABRASF 2.04
- Há também o PDF "Códigos de Hash NFS-e 2.04" em `C:\Users\bruno.reis\Downloads\` (escaneado — entra em jogo no momento da assinatura/validação)

## 7. Histórico de bugs do schema (referência para o backend nacional)

Mantido para diagnóstico se algum desses ressurgir num upgrade futuro da `nfelib`. **Já corrigidos** em `src/nfse_df.py`.

| # | Sintoma | Causa | Correção |
|---|---|---|---|
| 1 | E160 | `<opConsumServ>` em `<locPrest>` (removido no XSD v1.01) | Removido via patch lxml |
| 2 | E160 | `<totTrib>` com 2 filhos (choice exclusivo) | Mantém só `pTotTribSN` |
| 3 | E160 | `<nDPS>` com zeros à esquerda (pattern `[1-9][0-9]{0,14}`) | `_proximo_numero_dps()` retorna `str(int)` sem padding |
| 4 | E160 | `<verAplic>` > 20 chars | Encurtado para "iugu-nfse-df-0.3" |
| 5 | E160 | Grupo `<IBSCBS>` ausente | Injetado via lxml |
| — | E043 | "IM não cadastrada" em homologação Padrão Nacional | Resolvido pela habilitação cadastral de 05/06/2026 |
| — | E160 ABRASF | `<Signature>` em posição inválida (filha de `GerarNfseEnvio` em vez de `Rps`) | `_reposicionar_signature_dentro_de_rps` aplicado após `_assinar_xml` no fluxo ABRASF |

## 8. Pendências de segurança (avisar o Bruno no início)

1. 🔴 **Rotacionar credenciais expostas no chat ao longo do projeto:**
   - `IUGU_API_TOKEN` (já rotacionado uma vez; revisar)
   - `IUGU_WEBHOOK_TOKEN` (re-vazou num log paste — precisa re-rotação)
   - **Senha do certificado A1** (`mega10` — fraca)
   - Senha SMTP
2. 🟡 Confirmar `API_JWT_SECRET` forte e fixo no `.env` da VPS.
3. 🟡 Reduzir validade do JWT de 72h para 15–60min + refresh (ressalva da revisão de segurança).
4. 🟡 Adicionar **CSP** no vhost Apache (já temos HSTS/X-Frame/etc.) — mitiga o JWT em `localStorage` do painel.
5. 🟡 AMI/SIP do Asterisk expostos em `0.0.0.0` na mesma VPS — fora do escopo deste projeto, mas é o maior risco *da máquina*.

## 9. Estilo de colaboração com o Bruno

- Português BR, passo a passo, sem jargão desnecessário.
- Windows 11 + PowerShell (usar `curl.exe`, não `curl`).
- Forte em Python; às vezes confunde tokens/IDs (confirmar tipo quando ambíguo).
- Tem costume de colar credenciais no chat — avisar educadamente quando acontecer.
- **Preferência de teste:** por fluxo de negócio (fatura → pago → webhook → NFS-e → email), não por camada isolada.
- **SEMPRE usar `AskUserQuestion`** antes de iniciar tarefa multi-passo.

## 10. Decisões de produto já tomadas (evite re-perguntar)

- **Operação SOAP (ambos os backends):** síncrona, 1 documento → 1 resposta (`GerarNfse`).
- **Assinatura:** XMLDSig enveloped, C14N, RSA-SHA1, SHA1; certificado A1 SERPRORFB (válido até 05/03/2027).
- **Empresas (multi-cliente):** fonte é a **Iugu** (campo `notes` JSON em cada customer). Indexação por `customer_id` (não CNPJ — um CNPJ pode ter múltiplos customers/"departamentos"). ADR-0003 Etapa 1 deployada; Etapa 2 (rotas/app por `customer_id`) pendente.
- **Persistência:** ainda sem banco — estado em arquivos `nfse_emitidas/*.xml`. ADR-0001 (SQLite) proposto.
- **Webhook:** processa síncrono; falha recuperável → 502 (Iugu re-tenta), falha terminal/sucesso → 200 (WEB-010). Rejeição não é mais mascarada como sucesso (WEB-011).

## 11. Checklist de primeira ação na próxima sessão

1. ✅ Alertar pendências de credenciais (seção 8).
2. ✅ Confirmar estado real lendo `CLAUDE.md` + `docs/fase2_nfse_df.md` + `docs/adr/README.md`.
3. ✅ Próximo passo da Fase 2: gerar RPS de exemplo e enviar para `integracao.df@notacontrol.com.br` (seção 4).
4. ✅ Para retomar o roadmap arquitetural: começar pelo **ADR-0001 (SQLite)** — é a fundação dos ADRs 0002/0004.

---

**Resumo:** Fase 1 e Fase 3 no ar. Fase 2 com os dois backends (nacional + ABRASF) prontos estruturalmente; falta validar 1 RPS com o Nota Control e ajustar constantes específicas do ISSnet DF. Roadmap arquitetural rastreado em `docs/adr/`.
