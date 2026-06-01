"""Gerencia o estado de sessao persistente (item 3).

O ``session_state.json`` acumula contexto ao longo da run: data in-game, stats,
ranks de Confidant, flags de eventos, acoes do dia e avisos pendentes. Ele
persiste entre sessoes do app.

Regras de design:
  - Merge nao-destrutivo: leituras parciais da memoria (apenas o que esta mapeado)
    atualizam o estado sem zerar o que ja existia.
  - Deteccao de virada de dia: ao mudar a data in-game, ``today_actions_used`` e
    reiniciado automaticamente.
  - Nunca escreve no save do jogo; apenas no proprio JSON local.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config_manager import PROJECT_ROOT

STATE_PATH = PROJECT_ROOT / "session_state.json"

DEFAULT_STATE: dict[str, Any] = {
    "game": "Persona 5 Royal",
    "last_updated": None,
    "in_game_date": {"month": None, "day": None, "period": None},
    "social_stats": {
        "knowledge": None,
        "charm": None,
        "guts": None,
        "kindness": None,
        "proficiency": None,
    },
    "confidants": {},
    "yen": None,
    "key_flags": {
        "lottery_ticket_bought": False,
        "lottery_check_date": None,
        "billiards_available": False,
        "darts_available": False,
    },
    "today_actions_used": [],
    "pending_warnings": [],
    "guide_url": "https://psnprofiles.com/guide/11946-persona-5-royal-100-perfect-schedule",
    "guide_cache": {},
    # Origem de cada campo: "manual" (digitado pelo usuario) ou "memory" (lido ao vivo).
    # A leitura de memoria sempre assume quando o offset estiver mapeado.
    "data_source": {},
}

# Confidants do P5R (nomes canonicos) para a entrada manual.
CONFIDANT_NAMES = [
    "Morgana", "Ryuji", "Ann", "Yusuke", "Makoto", "Futaba", "Haru",
    "Akechi", "Kasumi", "Maruki", "Sojiro", "Kawakami", "Takemi", "Ohya",
    "Chihaya", "Mishima", "Hifumi", "Shinya", "Toranosuke", "Sae", "Iwai", "Yoshida",
]

SOCIAL_STAT_KEYS = ["knowledge", "charm", "guts", "kindness", "proficiency"]

# Origens de leitura ao vivo: sempre assumem sobre a entrada manual.
LIVE_SOURCES = {"memory", "save", "ocr"}


def _deep_merge(base: dict, incoming: dict) -> dict:
    """Merge recursivo: valores de ``incoming`` sobrescrevem, dicts sao fundidos.

    Valores ``None`` em incoming NAO sobrescrevem um valor existente em base
    (evita apagar dados quando a leitura parcial nao tem o campo).
    """
    out = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        elif value is None and out.get(key) is not None:
            continue
        else:
            out[key] = value
    return out


class StateManager:
    """Carrega, atualiza e persiste o session_state.json."""

    def __init__(self, path: Path | str = STATE_PATH):
        self.path = Path(path)
        self.state: dict[str, Any] = self.load()

    # ---- persistencia --------------------------------------------------
    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.state = copy.deepcopy(DEFAULT_STATE)
            self.save()
            return self.state
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Garante que chaves novas existam mesmo em estados antigos.
        self.state = _deep_merge(copy.deepcopy(DEFAULT_STATE), data or {})
        return self.state

    def save(self) -> None:
        self.state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        tmp.replace(self.path)  # escrita atomica

    # ---- atualizacao ---------------------------------------------------
    def apply_partial(self, partial: dict[str, Any], source: str = "memory") -> bool:
        """Funde uma leitura parcial ao vivo (memoria, save ou ocr).

        ``source`` registra a origem de cada campo ("memory"/"save"/"ocr") e essas
        origens sempre assumem sobre "manual". Retorna True se houve virada de dia.
        """
        old_date = dict(self.state.get("in_game_date") or {})
        self.state = _deep_merge(self.state, partial)

        # Marca a origem de tudo que veio da leitura ao vivo — assume sobre manual.
        src = self.state.setdefault("data_source", {})
        if "social_stats" in partial:
            for k in partial["social_stats"]:
                src[f"social_stats.{k}"] = source
        if "confidants" in partial:
            for k in partial["confidants"]:
                src[f"confidants.{k}"] = source
        if "yen" in partial:
            src["yen"] = source
        if "in_game_date" in partial:
            src["in_game_date"] = source

        day_rolled = False
        new_date = self.state.get("in_game_date") or {}
        if self._date_changed(old_date, new_date):
            day_rolled = True
            self.state["today_actions_used"] = []
        return day_rolled

    # ---- entrada manual ------------------------------------------------
    def set_manual_stats(self, stats: dict[str, Any]) -> None:
        """Define social stats digitados pelo usuario (fallback ate mapear memoria).

        Nao sobrescreve campos cuja origem ja e 'memory' (leitura ao vivo manda).
        """
        target = self.state.setdefault("social_stats", {})
        src = self.state.setdefault("data_source", {})
        for k, v in stats.items():
            if v is None:
                continue
            if src.get(f"social_stats.{k}") in LIVE_SOURCES:
                continue  # leitura ao vivo tem prioridade
            target[k] = v
            src[f"social_stats.{k}"] = "manual"

    def set_manual_confidants(self, confidants: dict[str, Any]) -> None:
        """Define ranks de confidant digitados pelo usuario (fallback)."""
        target = self.state.setdefault("confidants", {})
        src = self.state.setdefault("data_source", {})
        for name, rank in confidants.items():
            if rank is None:
                continue
            if src.get(f"confidants.{name}") in LIVE_SOURCES:
                continue
            target[name] = rank
            src[f"confidants.{name}"] = "manual"

    def set_manual_date(self, month: int, day: int, period: Optional[str] = None) -> None:
        """Define a data manualmente (caso o jogo nao esteja rodando / nao mapeado)."""
        src = self.state.setdefault("data_source", {})
        if src.get("in_game_date") in LIVE_SOURCES:
            return
        d = self.state.setdefault("in_game_date", {})
        d["month"], d["day"] = month, day
        if period is not None:
            d["period"] = period
        src["in_game_date"] = "manual"

    def source_of(self, field: str) -> Optional[str]:
        """Retorna 'manual', 'memory' ou None para um campo (ex.: 'social_stats.knowledge')."""
        return (self.state.get("data_source") or {}).get(field)

    @staticmethod
    def _date_changed(old: dict, new: dict) -> bool:
        if not old.get("month") and not old.get("day"):
            return False  # primeira leitura nao conta como virada
        return (old.get("month"), old.get("day")) != (
            new.get("month"),
            new.get("day"),
        )

    def add_action(self, action: str) -> None:
        if action not in self.state["today_actions_used"]:
            self.state["today_actions_used"].append(action)

    def set_flag(self, key: str, value: Any) -> None:
        self.state.setdefault("key_flags", {})[key] = value

    def set_warnings(self, warnings: list[dict]) -> None:
        self.state["pending_warnings"] = warnings

    def cache_guidance(self, guidance: dict) -> None:
        """Armazena a ultima orientacao da API para exibicao offline."""
        self.state["last_guidance"] = guidance

    # ---- acesso conveniente -------------------------------------------
    @property
    def date_str(self) -> str:
        d = self.state.get("in_game_date") or {}
        m, day, period = d.get("month"), d.get("day"), d.get("period")
        if m is None or day is None:
            return "data desconhecida"
        base = f"{m}/{day}"
        return f"{base} - {period}" if period else base

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)
