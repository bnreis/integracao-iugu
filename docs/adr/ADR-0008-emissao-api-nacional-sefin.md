# ADR-0008 — Emissão de NFS-e pela API da Sefin Nacional (REST)

- **Status:** 🟡 **Em produção na MegaTeam desde 2026-11-12** (`NFSE_PADRAO=nacional`); primeira nota real pendente de conferência.
- **Contexto:** desde 01/11/2026 a ISSnet (ABRASF 2.04) recusa RPS de optante do Simples Nacional
  com competência ≥ 11/2026 (`L120`). Toda emissão da MegaTeam falhava desde 05/11/2026.
  Diagnóstico e provas: `docs/incidente_l120_simples_nacional_2026-11.md`.
- **Relação:** complementa o ADR-0005 (dual ABRASF/nacional). O backend `nacional` deixa de usar o
  SOAP `wsnfsenacional` da ISSnet (responde 403/404) e passa a falar com a **Sefin Nacional**.
  Não altera o ADR-0006 (guardrail e lock por fatura) — `src/nfse_guard.py` não foi tocado.

---

## Decisão

Com `NFSE_PADRAO=nacional`, `_emitir_nacional` (`src/nfse_df.py`) monta a DPS como antes, ajusta
ao leiaute da Sefin, assina com o A1 e envia por **REST com mTLS**:

| Operação | Chamada | Resposta que importa |
|---|---|---|
| Emitir | `POST {base}/nfse` com `{"dpsXmlGZipB64": base64(gzip(DPS assinada))}` | `201` com `chaveAcesso` + `nfseXmlGZipB64`; `400` com `erros[]` |
| "Esta DPS já virou nota?" | `GET {base}/dps/{Id da DPS}` | `200` com `chaveAcesso`; `404` `E2404` se não existe |
| Baixar a nota | `GET {base}/nfse/{chave}` | `200` com `nfseXmlGZipB64`; `404` `E2401` |

`base` = `NFSE_SEFIN_URL_PRODUCAO` (`https://sefin.nfse.gov.br/SefinNacional`) ou
`NFSE_SEFIN_URL_HOMOLOGACAO` (`https://sefin.producaorestrita.nfse.gov.br/SefinNacional`), conforme
`NFSE_AMBIENTE`. TLS com a verificação padrão (nunca `verify=False`).

### Leiaute

`_ajustar_dps_para_sefin` aplica as diferenças entre a nossa DPS (XSD Nota Control v1.01) e a Sefin
1.6.0; `_normalizar_cTribNac` assume desdobro `01` quando o cadastro traz só item e subitem
(`01.07` → `010701`). A tabela completa, com o erro que revelou cada ajuste, está na seção 8 do
incidente. O grupo `IBSCBS` não é enviado (aceito para Simples Nacional).

### Numeração e não duplicar nota

- Número de DPS **reservado por fatura** em `.contador_dps.json` (mesmo mecanismo do RPS). A
  reserva **não expira** e só é liberada depois que o índice `nfse_<fatura>.json` foi gravado.
- Antes de enviar, pergunta à Sefin se aquela DPS já virou nota. Só envia com `404`.
- Se a nota já existe **e o número já estava reservado para a fatura** (tentativa anterior que deu
  timeout depois do aceite), baixa a nota e a adota — sem novo envio — desde que tomador e valor
  confiram.
- Se a nota já existe e o número acabou de ser alocado, o contador local está atrasado: não adota,
  solta o número e a próxima tentativa usa o seguinte.
- A Sefin ainda recusa DPS repetida com `E0014` (provado em homologação).

### Competência retroativa

Competência anterior a 11/2026 continua indo para o ABRASF, que ainda aceita, mesmo com
`NFSE_PADRAO=nacional`.

### E-mail e painel

`codigo_verificacao` recebe a chave de acesso (50 dígitos). O e-mail da nota nacional leva o XML
oficial e o link de consulta pública (`_CONSULTA_NACIONAL` em `src/email_nfse.py`), onde o tomador
baixa o PDF; não há PDF gerado localmente.

## Operação

- **Emitir só pela VPS.** O contador de DPS é por máquina, como o de RPS.
- **"Contador de DPS desta máquina está atrasado":** o código já se corrige sozinho, um número por
  tentativa. Se estiver muito atrás, ajuste `ultimo_numero` em `nfse_emitidas/.contador_dps.json`
  para o último número já usado (com o serviço parado) e tente de novo.
- **Timeout ao enviar:** a nota pode ter sido aceita. Tentar de novo pelo painel (a nova tentativa
  recupera a nota). **Não** emitir pelo portal nacional.
- **Tomador fora do DF** em cidade sem código IBGE em `_codigo_ibge_por_cidade`: a emissão é
  recusada antes de enviar; incluir a cidade na tabela.
- **Volta atrás:** `NFSE_PADRAO=abrasf204` + restart (o ABRASF segue recusando competência ≥ 11/2026).
- **Testar sem tocar produção:** `scripts/teste_api_nacional_homolog.py` numa cópia do código na VPS
  (seção 8.1 do incidente).

## Fora do escopo (não implementado)

- Cancelamento pela API (evento) e substituição.
- PDF oficial (DANFSE) anexado ao e-mail.
- Grupo `IBSCBS` preenchido (Reforma Tributária).
- Remoção do código SOAP nacional morto em `nfse_df.py`.

## Consequências

- A emissão deixa de depender do webservice municipal para competências a partir de 11/2026.
- Notas nacionais não têm "código de verificação" nem PDF local; consumidores que assumem o formato
  ABRASF (reenvio de nota marcada manualmente, índice de fallback) ainda usam o modelo antigo de e-mail.
- Pendências menores conhecidas (revisão de código de 12/11/2026, não corrigidas):
  - nota **recuperada** grava no índice a data de hoje, não a da nota — recuperação feita em mês
    seguinte faria o guardrail barrar a nota legítima daquele mês para o cliente;
  - nota retroativa despachada ao ABRASF é registrada com `padrao="nacional"`;
  - `scripts/emitir_nfse_manual.py --dry-run` no modo nacional não passa pelo ajuste de leiaute;
  - `_parsear_resposta_sefin` assume erro em formato de objeto; erro em texto puro vira mensagem técnica.
