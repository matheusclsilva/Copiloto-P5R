"""Utilidades de processo — detectar se o jogo esta em execucao.

Unica dependencia de runtime do antigo caminho de memoria que ainda faz sentido:
saber se o P5R.exe esta aberto (usado pelo poll de presenca do controller).
"""

from __future__ import annotations

try:
    import psutil  # type: ignore

    _PSUTIL_AVAILABLE = True
except Exception:
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False


def is_game_running(process_name: str = "P5R.exe") -> bool:
    """Retorna True se um processo com o nome dado estiver em execucao."""
    if not _PSUTIL_AVAILABLE:
        return False
    target = process_name.lower()
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == target:
            return True
    return False
