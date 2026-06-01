"""Acesso ao cronograma dia-a-dia do guia (fonte primaria do app).

O dataset ``guides/p5r_schedule.json`` e gerado por ``build_schedule.py`` a
partir do MHTML do guia PSNProfiles. Aqui apenas carregamos e consultamos:
data de hoje -> passos do dia (Day/Night), ja classificados por cor/categoria.

Categorias (kind) de cada tarefa:
  confidant | stat | dungeon | sidequest | item | normal
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .config_manager import PROJECT_ROOT

SCHEDULE_PATH = PROJECT_ROOT / "guides" / "p5r_schedule.json"
# Versao traduzida (gerada por translate_schedule.py). Se existir, tem prioridade.
SCHEDULE_PATH_PT = PROJECT_ROOT / "guides" / "p5r_schedule_pt.json"

# Tarefas que, se ignoradas, comprometem o 100% — destaque maximo.
CRITICAL_KINDS = {"item", "dungeon"}

# Rotulo curto por categoria, para exibicao.
KIND_LABEL = {
    "confidant": "Confidant",
    "stat": "Stat",
    "dungeon": "Palace",
    "sidequest": "Side quest",
    "item": "Item",
    "normal": "",
}

# Cor (hex) por categoria, alinhada a legenda do guia (para rich text na GUI).
KIND_COLOR = {
    "confidant": "#41a85f",  # verde
    "stat": "#2c82c9",       # azul
    "dungeon": "#d14841",    # vermelho
    "sidequest": "#777777",  # cinza
    "item": "#f37934",       # laranja
    "normal": "#222222",
}


@lru_cache(maxsize=1)
def load_schedule(path: str | Path | None = None) -> dict[str, Any]:
    """Carrega o cronograma. Prefere a versao pt-BR se ela existir.

    Se ``path`` for explicito, usa-o. Senao, usa ``p5r_schedule_pt.json`` quando
    presente (gerado por translate_schedule.py); caso contrario, o original em
    ingles. Assim o app fica em pt-BR automaticamente apos rodar o tradutor.
    """
    if path is None:
        path = SCHEDULE_PATH_PT if SCHEDULE_PATH_PT.exists() else SCHEDULE_PATH
    p = Path(path)
    if not p.exists():
        return {"days": {}, "month_intro": {}, "color_legend": {}}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def day_key(month: Optional[int], day: Optional[int]) -> Optional[str]:
    if month is None or day is None:
        return None
    return f"{int(month)}/{int(day)}"


def day_entry(month: Optional[int], day: Optional[int]) -> Optional[dict[str, Any]]:
    """Retorna a entrada do cronograma para a data, ou None se nao houver."""
    key = day_key(month, day)
    if key is None:
        return None
    return load_schedule().get("days", {}).get(key)


def month_intro(month: Optional[int]) -> str:
    if month is None:
        return ""
    return load_schedule().get("month_intro", {}).get(str(int(month)), "")


def _is_night(period: Optional[str]) -> bool:
    if not period:
        return False
    p = period.lower()
    return any(w in p for w in ("night", "evening", "noite"))


def current_period_tasks(entry: dict[str, Any], period: Optional[str]) -> list[dict[str, str]]:
    """Tarefas do periodo atual: Night se o periodo for noturno, senao Day.

    Se o periodo for desconhecido, retorna as tarefas do Day (o grosso do dia).
    """
    if _is_night(period):
        return entry.get("night") or []
    return entry.get("day") or []


def critical_tasks(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Tarefas que nao podem ser perdidas (itens laranja / infiltracoes)."""
    out: list[dict[str, str]] = []
    for slot in ("day", "night"):
        for t in entry.get(slot) or []:
            if t.get("kind") in CRITICAL_KINDS:
                out.append(t)
    return out


def summarize_period(tasks: list[dict[str, str]], limit: int = 3) -> str:
    """Resumo curto (texto plano) das primeiras tarefas, para o bloco AGORA."""
    if not tasks:
        return "Nada agendado neste periodo."
    parts = [t["text"] for t in tasks[:limit]]
    extra = len(tasks) - limit
    text = " | ".join(parts)
    if extra > 0:
        text += f"  (+{extra})"
    return text
