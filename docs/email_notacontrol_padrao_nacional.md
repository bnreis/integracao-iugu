# Rascunho de e-mail — Esclarecimento Padrão Nacional (DPS) x ABRASF 2.04 (Fase 2)

> **Contexto:** o chamado foi resolvido — a MEGASUPORTE foi **habilitada em produção**, e o Nota Control
> indicou o webservice **ABRASF 2.04** (`df.issnetonline.com.br/webservicenfse204/nfse.asmx`, RPS série 3).
> Porém nossa integração já está pronta para o **Padrão Nacional (DPS v1.01)**. Como o Padrão Nacional
> vira obrigatório em **30/06/2026**, queremos confirmar se podemos ir direto a ele e evitar construir
> o ABRASF 2.04 só para descontinuá-lo em seguida.
> **Status:** rascunho — revisar assinatura antes de enviar.

---

**Para:** integracao.df@notacontrol.com.br
*(cópia: suporte.df@notacontrol.com.br · tel. (67) 3041-2075)*

**Assunto:** NFS-e MEGASUPORTE (CNPJ 36.342.291/0001-43) — Padrão Nacional (DPS) x ABRASF 2.04 e validação de XML

---

Prezados, bom dia.

Agradecemos a **habilitação em produção** da **MEGASUPORTE SERVIÇOS DE TI LTDA** (CNPJ **36.342.291/0001-43**, Brasília/DF). Vocês nos indicaram o webservice **ABRASF 2.04** (`df.issnetonline.com.br/webservicenfse204/nfse.asmx`, **RPS série 3**).

Ocorre que nossa integração já está **desenvolvida e validada para o Padrão Nacional (DPS v1.01)** — XML da DPS passando na validação de schema, assinatura ICP-Brasil A1 (XMLDSig) e envelope SOAP `GerarNfse`. Diante disso, gostaríamos de confirmar dois pontos:

1. **Podemos integrar/emitir já pelo webservice do Padrão Nacional (DPS)** em produção para o DF? Existe endpoint do Padrão Nacional habilitado para o nosso CNPJ? (Ao testarmos `https://nfse.issnetonline.com.br/wsnfsenacional/producao/nfse.asmx` recebemos **HTTP 404**.) Como o Padrão Nacional passa a ser **obrigatório em 30/06/2026**, preferimos integrá-lo diretamente e evitar implementar o ABRASF 2.04 para descontinuá-lo logo em seguida.

2. **Caso a produção atual seja exclusivamente ABRASF 2.04** (`webservicenfse204`) até 30/06/2026, confirmam:
   a. a **operação recomendada** (`RecepcionarLoteRpsSincrono` ou `GerarNfse`);
   b. o uso de **RPS série 3**;
   c. que podemos enviar um **XML de teste para validação** neste canal (`integracao.df@notacontrol.com.br`) antes de emitir em produção?

Ficamos à disposição.

Atenciosamente,
**Bruno Reis**
MEGASUPORTE SERVIÇOS DE TI LTDA
[telefone] · [e-mail]
