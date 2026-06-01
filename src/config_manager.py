"""Gerenciamento de configuração e detecção automática do save do P5R.

Responsabilidades (item 1 da ordem de implementação):
  - Carregar/criar o ``config.json`` com valores padrão.
  - Resolver ``save_path`` quando configurado como ``"auto"``, detectando o
    diretório de saves do P5R via ``%APPDATA%\\SEGA\\P5R\\Steam\\``.
  - Listar os Steam IDs encontrados e montar o caminho completo do ``savedata``.

Nada aqui escreve no save do jogo — apenas leitura passiva de caminhos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Caminho padrão do config, relativo à raiz do projeto (pasta acima de src/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

# Subcaminho relativo ao %APPDATA% onde o P5R (Steam) guarda os saves.
P5R_STEAM_SUBPATH = Path("SEGA") / "P5R" / "Steam"

DEFAULT_CONFIG: dict = {
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    "deepseek_base_url": "https://api.deepseek.com",
    "save_path": "auto",
    "save_slot": "DATA01",
    "poll_interval_seconds": 10,
    "game": "p5r",
}


@dataclass
class SaveLocation:
    """Resultado da detecção do save do P5R."""

    base_dir: Optional[Path] = None          # ...\SEGA\P5R\Steam
    steam_ids: list[str] = field(default_factory=list)
    selected_steam_id: Optional[str] = None
    savedata_dir: Optional[Path] = None      # ...\Steam\{ID}\savedata
    slot_file: Optional[Path] = None         # ...\savedata\{SLOT}\DATA.DAT
    found: bool = False
    message: str = ""


def get_appdata_dir() -> Optional[Path]:
    """Retorna o diretório Roaming AppData do usuário, ou None se indisponível."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    # Fallback para o layout padrão do Windows.
    candidate = Path.home() / "AppData" / "Roaming"
    return candidate if candidate.exists() else None


def detect_p5r_steam_base() -> Optional[Path]:
    """Localiza ``%APPDATA%\\SEGA\\P5R\\Steam`` se existir."""
    appdata = get_appdata_dir()
    if appdata is None:
        return None
    base = appdata / P5R_STEAM_SUBPATH
    return base if base.is_dir() else None


def list_steam_ids(base_dir: Path) -> list[str]:
    """Lista subpastas que parecem Steam IDs (apenas dígitos, 17 chars típicos).

    Aceita qualquer subpasta numérica para ser tolerante a variações; ordena
    de forma estável para detecção determinística.
    """
    ids: list[str] = []
    for entry in sorted(base_dir.iterdir()):
        if entry.is_dir() and entry.name.isdigit():
            ids.append(entry.name)
    return ids


def resolve_save_location(config: dict) -> SaveLocation:
    """Resolve o local do save a partir da config.

    - Se ``save_path`` == ``"auto"``: detecta via %APPDATA%.
    - Caso contrário: usa o caminho informado como ``savedata`` (ou pai dele).

    Sempre tenta apontar para ``{savedata}\\{save_slot}\\DATA.DAT``.
    """
    slot = config.get("save_slot", "DATA01")
    raw_path = config.get("save_path", "auto")

    loc = SaveLocation()

    if raw_path and raw_path != "auto":
        manual = Path(raw_path).expanduser()
        loc = _resolve_savedata_dir(manual, slot)
        if not loc.found:
            loc.message = f"Caminho manual não encontrado ou inválido: {manual}"
        return loc

    # Modo automático.
    base = detect_p5r_steam_base()
    if base is None:
        loc.message = (
            "Save do P5R não encontrado em %APPDATA%\\SEGA\\P5R\\Steam. "
            "Rode o jogo ao menos uma vez ou defina 'save_path' manualmente no config.json."
        )
        return loc

    loc.base_dir = base
    loc.steam_ids = list_steam_ids(base)

    if not loc.steam_ids:
        loc.message = f"Nenhum Steam ID encontrado em {base}."
        return loc

    # Escolhe o primeiro Steam ID por padrão (caso de usuário único).
    loc.selected_steam_id = loc.steam_ids[0]
    savedata = base / loc.selected_steam_id / "savedata"
    sub = _resolve_savedata_dir(savedata, slot)
    loc.savedata_dir = sub.savedata_dir
    loc.slot_file = sub.slot_file
    loc.found = sub.found

    if loc.found:
        extra = ""
        if len(loc.steam_ids) > 1:
            extra = (
                f" (encontrados {len(loc.steam_ids)} Steam IDs; usando o primeiro: "
                f"{loc.selected_steam_id})"
            )
        loc.message = f"Save detectado em {loc.savedata_dir}{extra}"
    else:
        loc.message = (
            f"Pasta savedata esperada não encontrada para Steam ID "
            f"{loc.selected_steam_id}: {savedata}"
        )
    return loc


def _resolve_savedata_dir(path: Path, slot: str) -> SaveLocation:
    """Normaliza um caminho para a pasta ``savedata`` e localiza o slot.

    Aceita que ``path`` já seja a pasta ``savedata`` ou um pai que a contenha.
    """
    loc = SaveLocation()

    candidates = [path]
    if path.name.lower() != "savedata":
        candidates.append(path / "savedata")

    for cand in candidates:
        if cand.is_dir():
            loc.savedata_dir = cand
            slot_file = cand / slot / "DATA.DAT"
            loc.slot_file = slot_file
            loc.found = slot_file.is_file() or cand.is_dir()
            return loc

    return loc


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Carrega o config.json, criando-o com padrões se não existir.

    Faz merge dos padrões para tolerar configs antigos sem chaves novas.
    """
    path = Path(path)
    if not path.exists():
        save_config(DEFAULT_CONFIG, path)
        return dict(DEFAULT_CONFIG)

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    merged = dict(DEFAULT_CONFIG)
    merged.update(data or {})
    return merged


def save_config(config: dict, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Persiste a config em disco (UTF-8, indentado)."""
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
