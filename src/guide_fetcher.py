"""Cache e acesso ao guia + missables hardcoded (item 8 parcial / suporte).

Funcoes:
  - Carregar os missables criticos hardcoded (guides/p5r_missables.json), que
    funcionam como rede de seguranca independente da API.
  - Calcular quais missables estao proximos da data in-game atual.
  - (Opcional) baixar e cachear trechos do guia PSNProfiles para dar contexto a
    DeepSeek. O download e best-effort; o app funciona sem ele.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config_manager import PROJECT_ROOT

MISSABLES_PATH = PROJECT_ROOT / "guides" / "p5r_missables.json"

# Ordem dos meses no calendario do jogo (abril -> marco do ano seguinte).
_MONTH_ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


def load_missables(path: Path | str = MISSABLES_PATH) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _to_ordinal(month: int, day: int) -> int:
    """Converte (mes, dia) numa posicao linear no calendario do jogo.

    Usa o indice do mes na ordem do jogo * 31 + dia, suficiente para comparar
    proximidade relativa (nao precisa ser dias-calendario exatos).
    """
    try:
        idx = _MONTH_ORDER.index(month)
    except ValueError:
        idx = month  # fallback defensivo
    return idx * 31 + day


def upcoming_missables(month: Optional[int], day: Optional[int],
                       horizon_days: int = 14,
                       missables: Optional[list[dict]] = None) -> list[dict[str, Any]]:
    """Retorna missables a partir de hoje dentro do horizonte, com 'urgency_days'.

    Se a data atual for desconhecida, retorna a lista inteira sem urgencia.
    """
    items = missables if missables is not None else load_missables()
    if month is None or day is None:
        return [dict(m, urgency_days=None) for m in items]

    today = _to_ordinal(month, day)
    result: list[dict[str, Any]] = []
    for m in items:
        target = _to_ordinal(m.get("month"), m.get("day"))
        delta = target - today
        if 0 <= delta <= horizon_days:
            entry = dict(m)
            entry["urgency_days"] = delta
            result.append(entry)
    result.sort(key=lambda e: e["urgency_days"])
    return result


def guide_context_for_date(month: Optional[int], day: Optional[int],
                           guide_cache: Optional[dict] = None) -> str:
    """Monta um contexto textual para a DeepSeek a partir do cache do guia.

    Por ora usa os missables proximos como contexto minimo confiavel. Quando o
    download do guia PSNProfiles estiver implementado, o trecho do dia entra aqui.
    """
    upcoming = upcoming_missables(month, day)
    if not upcoming:
        return ""
    lines = ["Missables/avisos proximos (rede de seguranca hardcoded):"]
    for m in upcoming:
        u = m.get("urgency_days")
        when = f"em {u} dia(s)" if u is not None else "data nao calculada"
        lines.append(f"- [{m.get('type')}] {m.get('month')}/{m.get('day')} ({when}): {m.get('message')}")
    return "\n".join(lines)
