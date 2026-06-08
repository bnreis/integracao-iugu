# Análise — Integração com portais nacionais (NF-e × NFS-e)

> **Origem:** pesquisa do Bruno (08/06/2026) sobre o site `https://www.nfe.fazenda.gov.br/portal/principal.aspx`,
> com a hipótese de integrá-lo ao nosso sistema para visualizar a NFS-e emitida (parecia "melhor e mais atualizado").
> **Resultado:** este documento é só **análise** (nada foi desenvolvido). Retomar perto da virada do DF para o
> Padrão Nacional (**prazo 30/06/2026**).

---

## TL;DR

- `nfe.fazenda.gov.br` é o portal da **NF-e (mercadorias, modelo 55)** — **documento diferente do nosso**.
  **Não há integração possível** com a nossa NFS-e (serviços). **Descartar.**
- O portal nacional **certo** para serviços é **`nfse.gov.br`** (Padrão Nacional NFS-e / Ambiente de Dados
  Nacional – ADN). Esse sim é o "moderno e nacional".
- **O projeto já está arquitetado para integrar com ele**: o backend `_emitir_nacional` (DPS v1.01) é
  exatamente o Padrão Nacional (ADR-0005). A virada do DF é "trocar 1 variável" (`NFSE_PADRAO=nacional`).
- Quando no Padrão Nacional, destrava: consulta/visualização pela **chave de acesso (50 dígitos)** e o
  **DANFSe (PDF oficial) via API do ADN** — que resolveria a pendência diferida do `ConsultarUrlNfse`.

---

## 1. A pegadinha: NF-e ≠ NFS-e

| | **NF-e** (`nfe.fazenda.gov.br`) | **NFS-e** (o nosso) |
|---|---|---|
| O que documenta | Circulação de **mercadorias/produtos** (modelo 55) | **Serviços** (ISS, LC 116/2003) |
| Gestão | SEFAZ estaduais / Receita | Municípios → Padrão Nacional (Receita/Serpro) |
| Chave de acesso | 44 dígitos | 50 dígitos (padrão nacional) |
| Portal | `nfe.fazenda.gov.br` | `nfse.gov.br` (nacional) / portal do município |
| Serve para a MEGASUPORTE? | ❌ Não (não vendemos mercadoria) | ✅ Sim (serviço de TI) |

➡️ O `nfe.fazenda.gov.br` **nunca exibirá** as notas que emitimos — sistema, documento e chave diferentes.
**Não seguir por esse caminho.**

## 2. O portal nacional relevante: `nfse.gov.br` (Padrão Nacional)

Equivalente nacional **para serviços**, com o **Ambiente de Dados Nacional (ADN)**:
- **Consulta Pública** (`nfse.gov.br/consultapublica`): visualiza a nota pela **chave de acesso (50 díg.)** ou por
  **CNPJ do emitente + série/número da DPS + município**.
- **DANFSe**: PDF oficial **padronizado nacional**, gerado a partir da chave — inclusive via **API do ADN**.

## 3. Como o nosso projeto já se conecta a isso

- **Hoje:** emitimos via **ISSnet / ABRASF 2.04** (municipal DF). Notas consultáveis em
  `iss.fazenda.df.gov.br` (é o link de verificação que já vai no e-mail ao tomador).
- **Pronto para o nacional:** backend `_emitir_nacional` (DPS v1.01) = **Padrão Nacional / `nfse.gov.br`**
  (ver `docs/adr/ADR-0005-abrasf-2.04-rps.md`). Virada do DF prevista para **30/06/2026** → no código é só
  `NFSE_PADRAO=nacional` no `.env`.

## 4. O que a integração com `nfse.gov.br` destrava (quando no Padrão Nacional)

1. **Visualizar/baixar a nota** no portal nacional por chave de acesso (consulta pública).
2. **DANFSe (PDF oficial) via API do ADN** — pela chave de acesso. **Resolveria a pendência diferida**
   do PDF oficial (o `ConsultarUrlNfse` do ISSnet, que dava E160): em vez da extensão do ISSnet, puxaríamos
   o PDF padronizado nacional. Permitiria **anexar o PDF no e-mail** (além do XML).
3. **Atualizar o link de verificação** do e-mail para o portal nacional (`nfse.gov.br/consultapublica`).

## 5. Ressalvas / a confirmar antes de qualquer decisão

- ⏳ **Depende da virada para o Padrão Nacional** (DF: 30/06/2026). Emitindo em **ABRASF 2.04** hoje, as
  notas provavelmente **ainda não aparecem** no `nfse.gov.br` — só no portal do DF.
- ❓ **A confirmar com Nota Control / DF:** se o DF já **replica** as NFS-e municipais atuais (ABRASF) para o
  **ADN nacional** antes da migração total. Isso define se dá para consultar as notas de hoje (#408, MEGATEAM)
  no portal nacional **já**, ou só após a virada.
- 🔑 A consulta nacional usa a **chave de acesso de 50 dígitos** (gerada no padrão nacional). As notas ABRASF
  atuais têm número/código municipal, não essa chave — outro motivo de não estarem lá ainda.
- 🔌 A **API do ADN** (DANFSe/consulta) tem **credenciamento e regras próprias** — exige análise técnica
  específica antes de integrar.

## 6. Recomendação

- **Descartar** `nfe.fazenda.gov.br` (documento errado).
- **Alvo de integração = `nfse.gov.br` (Padrão Nacional)** — já no roadmap via backend DPS v1.01.
- **Perto de 30/06/2026 (ou se o DF antecipar):**
  1. virar `NFSE_PADRAO=nacional`;
  2. avaliar a **API DANFSe do ADN** para puxar o PDF oficial pela chave (resolve a pendência do PDF);
  3. apontar o link de verificação do e-mail para o portal nacional.
- **Verificação útil agora:** perguntar ao Nota Control/DF se as notas ABRASF atuais já são replicadas ao ADN
  (define se conseguimos ver as notas de hoje no portal nacional antes da virada).

## 7. Fontes

- Portal Nacional da NF-e (mercadorias, modelo 55): https://www.nfe.fazenda.gov.br/portal/
- Diferença NF-e × NFC-e × NFS-e: https://ajuda.bling.com.br/hc/pt-br/articles/360035834073 ·
  https://qive.com.br/blog/diferencas-nfe-nfse
- Consulta Pública NFS-e Nacional: https://www.nfse.gov.br/consultapublica
- DANFSe da NFS-e Nacional (consultar/gerar PDF/baixar XML): https://espiaonfe.com.br/blog/danfse-nfse-nacional
- Documentação técnica oficial — Sistema Nacional NFS-e:
  https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/guia-emissorpubliconacionalweb_snnfse-ern-v12.pdf

---

*Relacionado:* `docs/adr/ADR-0005-abrasf-2.04-rps.md` (arquitetura dual), `docs/HANDOFF_OPUS46.md`
(pendência diferida do `ConsultarUrlNfse` / PDF oficial), `docs/relatorio-integracao-nfse-df.md`
(contexto ABRASF × Padrão Nacional).
