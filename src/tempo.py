"""Data/hora no fuso fiscal do Brasil (America/Sao_Paulo).

A VPS roda em UTC e NÃO pode ter o fuso alterado (compartilhada com o PBX). Entre
21h e 24h de Brasília o `date.today()` do servidor já é o dia seguinte e a ISSnet
rejeita o RPS com E016 ("data de emissão maior que hoje"). Toda data de negócio
(DataEmissao, Competência, mês do guardrail) deve vir daqui.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")


def agora_br() -> datetime:
    """Momento atual com fuso de Brasília."""
    return datetime.now(FUSO_BRASIL)


def hoje_br() -> date:
    """Data de hoje em Brasília (não a do relógio UTC do servidor)."""
    return agora_br().date()
