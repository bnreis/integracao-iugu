# Base de conhecimento — problemas identificados e como foram resolvidos

> **Regra do projeto:** todo problema investigado/resolvido vira um documento em
> `docs/incidente_<tema>_<AAAA-MM>.md` (modelo: `incidente_tls_issnet_2026-07.md`: resumo,
> sintoma com erro exato, impacto, diagnóstico com evidências, causa-raiz, correção, validação,
> pendências, prevenção) **e** ganha uma linha na tabela abaixo. Registre também o que **não**
> foi provado (hipóteses) e o que ficou pendente.

## Como usar

Procure o **sintoma** (mensagem de erro) na tabela. Se achar, abra o documento: ele traz o
diagnóstico passo a passo e a receita de correção.

## Índice por sintoma

| Data | Sintoma / erro exato | Causa-raiz (resumo) | Resolução | Documento |
|---|---|---|---|---|
| 2026-10-14 a 17 | `E232 Ocorreu um erro no processamento do arquivo`; `ReadTimeout`; contador de RPS disparando (99→189) | **ISSnet instável** (externo) + RPS consumido a cada reentrega do webhook + SOAP bloqueando o event loop | Reserva de RPS por fatura, SOAP em `to_thread`; ISSnet normalizou sozinha | [incidente_e232_issnet_rps_fuso_2026-10.md](incidente_e232_issnet_rps_fuso_2026-10.md) |
| 2026-10-14/15 | `E016 A data da emissão do RPS não poderá ser superior a data de hoje` (+ `L016`) à noite | VPS em UTC: entre 21h–24h (Brasília) `date.today()` já era "amanhã" | `src/tempo.py` (`hoje_br()`), incl. `nfse_guard.py:272`; fuso da VPS **não** alterado | mesmo documento acima |
| 2026-07-17 | Painel mostrava os mesmos dados nas duas empresas (MegaSuporte × MegaTeam) | `ProxyPass` do Apache apontando `/api /auth /webhook /health` para a porta 8001 | Corrigir vhost; diagnosticar **pelo domínio público autenticado**, conferir todas as linhas `ProxyPass` | [incidente_multiempresa_apache_2026-07.md](incidente_multiempresa_apache_2026-07.md) |
| 2026-06-30 a 07-14 | `[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain` em **todas** as emissões | ISSnet renovou o cert TLS (cadeia GoDaddy sem âncora no `certifi`/SO) | `NFSE_CA_BUNDLE_PATH` + bundle `certifi + cadeia GoDaddy` (**nunca** `verify=False`) | [incidente_tls_issnet_2026-07.md](incidente_tls_issnet_2026-07.md) |
| 2026-06-18 | `GET /v1/customers` (Iugu) retorna só 1 cliente | Bug da listagem da Iugu | Enumerar por busca + registro local + resolução por ID | [iugu_listagem_customers_contorno.md](iugu_listagem_customers_contorno.md) |
| 2026-06 | `E160` na emissão (schema v1.01 DPS) | `opConsumServ`, `totTrib`, `nDPS` com zeros, `verAplic` longo | Ver tabela "Histórico de bugs do schema v1.01" no `CLAUDE.md` | `CLAUDE.md` + `HANDOFF_OPUS46.md` |

## Códigos de retorno da ISSnet já vistos (ABRASF 2.04)

| Código | Mensagem | O que fazer |
|---|---|---|
| `E232` | Erro no processamento do arquivo | Se o XML é igual ao de uma nota aceita → externo; aguardar/abrir chamado. Não reemitir em loop. |
| `E016` | Data de emissão do RPS maior que hoje | Fuso (ver `src/tempo.py`). |
| `E010` | RPS já informado | A ISSnet já aceitou esse RPS → **conferir a nota no portal** e usar "Marcar como emitida"; não reemitir. |
| `E090` | Número do RPS inválido | Visto no RPS 51, quando a faixa autorizada era 1–50 → provável RPS fora da faixa (solicitar mais faixa no ISSnet). |
| `E160` | Erro de schema | Comparar com o XSD (`scripts/validar_dps_xsd.py`). |

## Modelo para um novo incidente

```markdown
# Incidente — <título curto> (<AAAA-MM-DD>)
- **Status / Severidade / Componente / Commits**
## 1. Resumo   ## 2. Sintoma (erro exato)   ## 3. Impacto
## 4. Diagnóstico (passo a passo, com evidências)   ## 5. Causa-raiz (separar externa × nossa; marcar hipóteses)
## 6. Correção aplicada   ## 7. Deploy e validação   ## 8. Pendências   ## 9. Como diagnosticar de novo   ## 10. Prevenção
```
