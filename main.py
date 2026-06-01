"""Platinum Assistant — entry point.

Uso:
    python main.py            # abre a GUI (janela flutuante + system tray)
    python main.py --check    # diagnostico no terminal (sem GUI)
    python main.py --headless # roda o controller uma vez e imprime o estado
"""

from __future__ import annotations

import sys


def _check() -> int:
    from src.config_manager import DEFAULT_CONFIG_PATH, load_config, resolve_save_location
    from src.memory_reader import MemoryReadError, read_live_date
    from src.process_utils import is_game_running

    print("=" * 52)
    print(" Platinum Assistant — diagnostico")
    print("=" * 52)

    config = load_config()
    print(f"\nConfig: {DEFAULT_CONFIG_PATH}")
    print(f"  modelo DeepSeek : {config.get('deepseek_model')}")
    api = "definida" if config.get("deepseek_api_key") else "VAZIA"
    print(f"  API key         : {api}")

    loc = resolve_save_location(config)
    print(f"\nSave: {loc.message}")
    if loc.slot_file:
        print(f"  slot: {loc.slot_file} [{'OK' if loc.slot_file.is_file() else 'ausente'}]")

    running = is_game_running(config.get("process_name", "P5R.exe"))
    print(f"\nP5R.exe rodando: {'sim' if running else 'nao'}")
    if running:
        try:
            live = read_live_date(config.get("process_name", "P5R.exe"))
            print(
                "  memoria         : "
                f"{live.month}/{live.day} - {live.period} "
                f"(counter={live.day_counter}, ptr=0x{live.target_address:X})"
            )
        except MemoryReadError as exc:
            print(f"  memoria         : falhou ({exc})")
    return 0


def _headless() -> int:
    from src.controller import Controller

    print("Rodando controller (uma passada)...")
    c = Controller()
    c.read_game_state()
    c.load_today_plan()
    print("Estado:", c.state_mgr.date_str)
    stats = c.state_mgr.state.get("social_stats")
    print("Stats :", stats)
    print("Yen   :", c.state_mgr.state.get("yen"))
    print("Jogo  :", "rodando" if c.status.game_running else "offline")

    # Fonte primaria: passos do dia vindos do guia.
    entry = c.status.schedule_today
    if entry is None:
        print("\nGuia: sem entrada para esta data.")
    else:
        def show(slot, label):
            tasks = entry.get(slot) or []
            print(f"\n{label}:")
            for t in tasks:
                k = t.get("kind")
                tag = f"[{k}] " if k and k != "normal" else ""
                print(f"  - {tag}{t.get('text')}")
        show("day", "DIA")
        show("night", "NOITE")

    up = c.status.upcoming
    print(f"\nMissables proximos: {len(up)}")
    for m in up:
        print(f"  - {m.get('month')}/{m.get('day')} ({m.get('urgency_days')}d): {m.get('message')}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args:
        return _check()
    if "--headless" in args:
        return _headless()
    from src.gui import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
