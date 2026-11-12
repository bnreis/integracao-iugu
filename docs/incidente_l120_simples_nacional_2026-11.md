# Incidente — L120: ISSnet bloqueia RPS (ABRASF) com competência ≥ 01/11/2026 para Simples Nacional (2026-11-05 →)

- **Status:** 🟠 **Diagnosticado, NÃO resolvido em produção** (atualizado em 2026-11-12). Caminho definitivo
  (opção C, API Nacional) **provado em homologação** — ver seção 8. Falta implementar no código e decidir as 5 faturas paradas.
- **Severidade:** Alta — **todas** as emissões da MegaTeam (ABRASF 2.04, optante Simples Nacional) falham desde 05/11.
- **Componente:** `src/nfse_df.py` (`_emitir_abrasf204`, competência = hoje).
- **Causa:** externa (mudança de regra da ISSnet/DF), previsível pelo cronograma do Padrão Nacional.

---

## 1. Sintoma (erro exato)

No app: a emissão não é permitida / "nota rejeitada". Retorno da ISSnet (`rps_<n>_retorno_*.xml`):

```
L120 Não é permitido emitir NFSe através deste serviço na data de competência informada.
     Para optantes Simples Nacional não é permitido RPS com data de competência maior ou
     igual à 01/11/2026.
Correção: A emissão deve ser realizada no Emissor Nacional ou através de sistemas de
     terceiros com comunicação com a API do Ambiente Nacional.
L039 Prestador não está autorizado a emitir nota com essa natureza de operação.   (consequência)
```

## 2. Linha do tempo / evidências (VPS, `journalctl` + `nfse_emitidas/`)

- Última NFS-e emitida com sucesso: **nº 68 em 26/10/2026** (competência 2026-10, RPS 195).
- Primeira falha L120: **05/11 (RPS 196)**. Depois: 09/11 (197), 11/11 (197, 198, 199), 12/11 (199).
- Todas as tentativas usam `Competencia = hoje` (novembro) → rejeitadas.
- Faturas paradas (RPS reservado, sem nota): `52952A51…` (196, desde 05/11), Star Corretora
  `03483C48…` (197), SINDICONDOMINIO-DF `A93CDD5E…` (198), Grau Técnico Brasília `AFFDDCC8…` (199),
  KEMMI `EBD52636…` (200 — esta retornou **E232**, não L120; possivelmente o E232 intermitente
  de [outubro](incidente_e232_issnet_rps_fuso_2026-10.md); não confirmado).
- ✅ Efeito colateral bom: o contador **não** queimou RPS (parado em 200; reservas 196–200 ativas) —
  a correção de 16/10 funcionou.

## 3. Causa-raiz

Regra nova do DF/ISSnet: o serviço **ABRASF 2.04 deixa de aceitar RPS de optante do Simples Nacional
com competência ≥ 01/11/2026**; essas notas só podem sair pelo **Emissor Nacional** ou por sistema
próprio integrado à **API do Ambiente Nacional (ADN)**. É o cronograma de migração para o Padrão
Nacional (antes dado como 30/06/2026, prorrogado) chegando à produção da MegaTeam. Nada no nosso XML
mudou — o código só nunca enfrentou competência de novembro.

### 3.1 Hipótese descartada: certificado A1 renovado (12/11)

O Bruno renovou o A1 e perguntou se seria isso. **Não é.** Evidências (VPS, 12/11):
- O `.pfx` em uso (`certs/megateam.pfx`, instalado em 16/07) é `MEGATEAM SERVICOS DE TI LTDA:27987745000142`,
  AC SERPRORFBv5, **válido de 15/07/2026 a 15/07/2027** (245 dias restantes) — é o mesmo que emitiu a nota nº 68 em 26/10.
- **Não está revogado**: consultado nas duas LCRs da AC (emitidas em 12/11) → serial ausente.
- Nenhum erro de certificado/assinatura/TLS no log dos últimos 20 dias.
- A ISSnet respondeu com regra de negócio (L120, citando o nº do RPS), não com erro de autenticação/assinatura.

Atenção: o certificado novo **não** foi instalado na VPS (nem precisa, enquanto o atual valer). Se a AC
revogar o antigo por causa da renovação, a emissão passará a falhar por certificado — aí sim trocar o
`.pfx` e a senha no `.env` da instância.

## 4. O que NÃO está pronto (descoberta importante)

A premissa do `CLAUDE.md`/ADR-0005 — "virada para o Padrão Nacional = trocar `NFSE_PADRAO=nacional`" —
**provavelmente não vale**. Nosso `_emitir_nacional` fala **SOAP no `wsnfsenacional` da ISSnet**, e esse
endpoint não existe para o nosso CNPJ: probe de 12/11 → `nfse.issnetonline.com.br/wsnfsenacional` **403**,
`df.issnetonline.com.br/wsnfsenacional` **404** (já era 404 em jun/2026, ver ADR-0005). O caminho exigido
agora é a **API REST do Ambiente Nacional (ADN)** — integração nova (mTLS com o A1 + DPS v1.01).
O construtor/assinador de DPS existente é reaproveitável; o **transporte e o parsing são outros**.
(`adn.nfse.gov.br` deu timeout da VPS — pode ser só rede/TLS; não conclusivo.)

## 5. Opções (decisão pendente — envolve contador)

| # | Opção | Prós / contras |
|---|---|---|
| A | **Emitir as pendentes no Emissor Nacional (portal gov.br/nfse)**, à mão | Imediato e legítimo; manual; depois marcar "NF-e já emitida" no app para o guardrail. |
| B | **"Emitir com competência…" = outubro** no app (feature `98c0b9b`) | Funciona tecnicamente (competência ≤ 31/10), mas só é correto se o serviço foi **prestado em outubro**; **validar com o contador** (ISS/PGDAS-D). Não serve para serviço de novembro. |
| C | **Implementar integração com a API do Ambiente Nacional (ADN)** | Solução definitiva; é projeto (homologação, mTLS, DPS, consulta, cancelamento, DANFSE). Ver ADR novo. ✅ **Viabilidade provada em homologação em 12/11 (seção 8).** |

## 6. Pendências

- Decidir A/B/C com o Bruno + contador; abrir ADR para C.
- Resolver as 5 faturas paradas (acima) e depois `Marcar como emitida` se feitas no portal nacional.
- Conferir se há **MegaSuporte** afetada (instância desativada) e demais empresas Simples Nacional.
- Atualizar `CLAUDE.md`/ADR-0005: remover a afirmação de que a virada é "trocar 1 variável".
- Se o E232 reaparecer em volume, seguir o postmortem de outubro.

## 8. Teste isolado da API Nacional em homologação (12/11) — ✅ funcionou

**Pergunta:** a opção C resolve de verdade? **Resposta: sim, em homologação.** Uma DPS de R$ 1,00 da
MegaTeam (Simples Nacional, DF, **competência 12/11/2026** — a mesma que a ISSnet recusa com L120) foi
aceita pela Sefin Nacional em *produção restrita*: **HTTP 201, `cStat 100`, NFS-e de teste nº 1**,
chave `53001082227987745000142000000000000126112332975926` (`ambGer=2`, sem valor fiscal).

Script: `scripts/teste_api_nacional_homolog.py` (roda na VPS via stdin; reaproveita `_montar_xml_dps`,
`_assinar_xml` e `_pkcs12_to_pem_tempfiles`; **não** usa contadores nem `nfse_emitidas/`, não toca produção).

**O que ficou provado**
- Rede da VPS alcança `sefin.producaorestrita.nfse.gov.br`; o **A1 da MegaTeam é aceito no mTLS**.
- Transporte: `POST https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse`, JSON
  `{"dpsXmlGZipB64": base64(gzip(DPS assinada))}`; resposta traz `chaveAcesso` + `nfseXmlGZipB64`.
- Assinatura atual (`erpbrasil`, RSA-SHA1, enveloped) é aceita **sem mudança**.
- Brasília está conveniada: `GET https://adn.producaorestrita.nfse.gov.br/parametrizacao/5300108/convenio`
  → `aderenteAmbienteNacional=1`, `aderenteEmissorNacional=1`. Serviço `01.07.01.001` com alíquota 2%
  **desde 27/10/2026** (`.../parametrizacao/5300108/01.07.01.001/<data>/aliquota`).
- ⚠️ O host `adn.*` derruba a conexão com TLS 1.3 (`Server disconnected`); com `maximum_version = TLSv1_2` responde.

**Diferenças de leiaute** — nossa DPS (XSD Nota Control v1.01) × Sefin Nacional 1.6.0. Cada uma foi
descoberta pelo erro devolvido; estão todas em `ajustar()` no script:

| # | Erro devolvido | Ajuste |
|---|---|---|
| 1 | `E1235` schema: `cLocEmi` inválido, esperado `finNFSe` | `finNFSe` vai direto em `infDPS`, logo após `tpEmit` (não dentro de `IBSCBS`) |
| 2 | `E1235` schema: `cTribMun` pattern | `cTribMun` tem 3 dígitos → `001` (o `1071` é do leiaute ISSnet) |
| 3 | `E1235` schema: `cNBS` inválido em `cServ` | `cNBS` vem **antes** de `xDescServ` |
| 4 | `E1235` schema: `gIBSCBS` inválido | `CST`/`cClassTrib` direto em `IBSCBS/valores/trib`, sem o wrapper `gIBSCBS` |
| 5 | `E1000` calculadora: indicador de operação `000000` não encontrado | Nosso `cIndOp` é placeholder. No teste o grupo `IBSCBS` foi **removido** (aceito p/ Simples Nacional) |
| 6 | `E0120` IM do prestador não deve ser informado | Remover `prest/IM` (DF não tem dados complementares no CNC — bate com a pista do CF/DF) |
| 7 | `E0121` nome do prestador não deve ser informado | Remover `prest/xNome` quando o emitente é o próprio prestador |
| 8 | `E0312` código de tributação não administrado pelo município | Resolvido pelo ajuste 2 (`010701` + `001`) |
| 9 | `E0625` alíquota não permitida p/ ME/EPP sem retenção | Remover `pAliq` quando `tpRetISSQN=1` |
| 10 | `E0310` código de tributação nacional não existe na lista nacional | O cadastro real traz `01.07`, que virava `010700`. Não existe desdobro `00`: `_normalizar_cTribNac` passa a assumir `01` (→ `010701`). Achado na prova com o código real (8.1) |

**O que ainda NÃO foi testado (antes de ligar em produção)**
- **Emissão** em produção (`POST`): o endpoint e a parametrização do DF já responderam à sonda de leitura (8.2).
- Cancelamento (evento) e **DANFSE (PDF)**; o link de consulta pública do e-mail (`nfse.gov.br/ConsultaPublica`).
- Grupo `IBSCBS` preenchido de verdade (`cIndOp` válido) — hoje só passou **sem** o grupo.
- Tomador fora do DF em cidade **fora** da tabela de `_codigo_ibge_por_cidade`: o código recusa antes de enviar
  (hoje as 9 empresas que emitem nota estão no DF).

**Trabalho para a opção C** (estimativa de escopo, não de prazo): novo transporte REST em `nfse_df.py`
(no lugar do SOAP `wsnfsenacional`), os ajustes acima no `_patch_xml_para_v101`/`_montar_xml_dps`, parsing
da resposta JSON (chave, nº, XML), link/PDF para o e-mail, integração com `nfse_guard`, e um teste de R$ 1,00 em produção.

### 8.1 Prova com o código real (`nfse_df.emitir_nfse`) — 12/11, ✅ os 6 casos passaram

Rodada numa **cópia separada** do código na VPS (`/tmp/homolog_nacional/src`, branch
`feat/emissao-api-nacional`), com o venv e o `.env` de produção só para leitura e saída em
`/tmp/homolog_nacional/saida`. O serviço não foi reiniciado nem atualizado; depois da rodada o
`.contador_rps.json` tinha o mesmo hash, `NFSE_PADRAO` seguia `abrasf204` e não havia `dps_*` nem
`.contador_dps.json` em `nfse_emitidas/`. Script: `scripts/teste_api_nacional_homolog.py` (env `CASO`).

**Levantamento do cadastro real (somente leitura):** 16 empresas ativas, 9 emitem nota; as 9 com
`codigo_servico = "01.07"`, alíquota 2%, todas no DF, todas com e-mail, **1 com ISS retido**.

| Caso | O que prova | Resultado |
|---|---|---|
| A | Fatura normal R$ 1,00, código `01.07` do cadastro, tomador com e-mail | 1ª rodada: `E0310` (ajuste 10). Corrigido → NFS-e nº 2, DPS 900101 (a rejeição **não** queimou o número) |
| B | ISS retido: `valor_bruto_nfse` R$ 2,00, `tpRetISSQN=2` + `pAliq` | NFS-e nº 3. A Sefin devolveu `vBC 2.00`, `vISSQN 0.04`, `vLiq 2.00` (não desconta o ISS retido do líquido) — conferir com o contador |
| C | Tomador de São Paulo/SP (`cMun 3550308`) | NFS-e nº 4; local de incidência continua Brasília |
| D | Recuperação: estado de "timeout depois do aceite" do caso A | `GET /dps/{id}` 200 → `GET /nfse/{chave}` 200 **com** `nfseXmlGZipB64` → mesma chave, **nenhum** POST |
| E | Reenvio direto da DPS já aceita | `400` **`E0014`** ("Série, Número, Município Emissor e CNPJ ... já existe em uma NFS-e") — a Sefin barra DPS repetida |
| F | Contador de DPS atrasado em 1 | 1ª tentativa recusa sem enviar ("contador de DPS desta máquina está atrasado"), 2ª usa o número seguinte → NFS-e nº 5 |

Notas de homologação geradas (sem valor fiscal): nº 2 a 5, DPS 900101–900104, série 00001.

### 8.2 Sonda de produção, somente leitura (12/11)

Só `GET`, da VPS, com o A1 da MegaTeam — nada foi emitido:

- `GET https://sefin.nfse.gov.br/SefinNacional/nfse/<chave inexistente>` → `404` `E2401` (`tipoAmbiente 1`,
  `SefinNacional_1.6.0`): o endpoint de produção aceita o nosso certificado e responde no mesmo formato.
- `GET .../dps/<Id da série 00001, números 1 e 2>` → `404` `E2404`: a numeração que o contador novo vai usar
  em produção (começa em 1) está livre.
- `GET https://adn.nfse.gov.br/parametrizacao/5300108/convenio` → `aderenteAmbienteNacional=1`,
  `aderenteEmissorNacional=1`; `.../01.07.01.001/<hoje>/aliquota` → `2.00` (desde 01/05/2025). TLS ≤ 1.2.

Falta só a emissão real: uma fatura escolhida pelo Bruno, com `NFSE_PADRAO=nacional` na MegaTeam.

## 7. Como diagnosticar de novo

```bash
cd /opt/integracao-iugu-megateam/nfse_emitidas
grep -l '<Codigo>L120' rps_*_retorno_*.xml | sed -E 's/rps_([0-9]+)_retorno_([0-9]{8}).*/\2 RPS \1/' | sort | uniq -c
cat .contador_rps.json     # 'pendentes' = faturas com RPS reservado e sem nota
```
