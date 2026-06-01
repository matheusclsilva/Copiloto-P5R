"""Valida os offsets da CheatTable v30 contra a memoria viva do P5R.

Uso (com o jogo ABERTO e um save CARREGADO, ja no gameplay):
    python -m tools.validate_ct_v30

Read-only: so le a memoria, nunca escreve. Imprime os valores que cada offset
do CT v30 produz para conferir contra o que o jogo mostra na tela (data,
periodo, yen, social stats).

CT v30 — base unica "Static Address Base (Money)[Steam]" = P5R.exe+0x28551FC:
    Money/yen          base +0x0       (4 bytes)
    Current Month/Day  base +0x7050    (2 bytes)
    Current Time       base +0x7052    (1 byte)
    Next Month/Day     base +0x7054    (2 bytes)
    Next Time          base +0x7056    (1 byte)
    Knowledge          base +0x101E0   (2 bytes)
    Charm              base +0x101E2   (2 bytes)
    Proficiency        base +0x101E4   (2 bytes)
    Guts               base +0x101E6   (2 bytes)
    Kindness           base +0x101E8   (2 bytes)
"""

from __future__ import annotations

import ctypes
import struct

from src.memory_reader import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    MemoryReadError,
    _find_process,
    _module_base,
    _read_process_memory,
    kernel32,
    read_live_date,
)
from src.save_reader import PERIOD_NAMES

CT_BASE_RVA = 0x28551FC
OFF_MONEY = 0x0
OFF_CUR_DATE = 0x7050
OFF_CUR_TIME = 0x7052
OFF_NEXT_DATE = 0x7054
OFF_NEXT_TIME = 0x7056
SOCIAL = {
    "Knowledge": 0x101E0,
    "Charm": 0x101E2,
    "Proficiency": 0x101E4,
    "Guts": 0x101E6,
    "Kindness": 0x101E8,
}


def _u8(b: bytes) -> int:
    return b[0]


def _u16(b: bytes) -> int:
    return struct.unpack("<H", b)[0]


def _u32(b: bytes) -> int:
    return struct.unpack("<I", b)[0]


def main() -> int:
    proc_name = "P5R.exe"
    try:
        proc = _find_process(proc_name)
        module_base = _module_base(proc, proc_name)
    except MemoryReadError as exc:
        print(f"[x] {exc}")
        print("    Abra o jogo e CARREGUE um save (entre no gameplay) antes de rodar.")
        return 1

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(proc.info["pid"])
    )
    if not handle:
        print(f"[x] OpenProcess falhou: erro {ctypes.get_last_error()}")
        return 1

    try:
        print(f"module_base = 0x{module_base:X}")

        # --- Referencia: leitura que o app ja usa hoje (ponteiro 0x286C188) ---
        print("\n=== Referencia atual (memory_reader, ponteiro 0x286C188) ===")
        try:
            live = read_live_date(proc_name)
            print(f"  data={live.month}/{live.day}  periodo={live.period} "
                  f"(code={live.period_code})  day_counter={live.day_counter}")
        except MemoryReadError as exc:
            print(f"  falhou: {exc}")

        # --- CT v30: base como ENDERECO DIRETO (os filhos somam o offset na base,
        #     sem dereferenciar — confirmado: yen lido direto em base+0x0). ---
        S = module_base + CT_BASE_RVA
        print(f"\n=== CT v30: P5R.exe+0x{CT_BASE_RVA:X} = 0x{S:X} (endereco direto) ===")

        def read(off, size):
            return _read_process_memory(handle, S + off, size)

        money = _u32(read(OFF_MONEY, 4))
        cur = read(OFF_CUR_DATE, 2)
        cur_time = _u8(read(OFF_CUR_TIME, 1))
        nxt = read(OFF_NEXT_DATE, 2)
        nxt_time = _u8(read(OFF_NEXT_TIME, 1))
        print(f"  Money/yen           = {money}")
        print(f"  Current Month/Day   = bytes {cur[0]}/{cur[1]}  (u16={_u16(cur)})")
        print(f"  Current Time        = {cur_time}  -> {PERIOD_NAMES.get(cur_time, '??')}")
        print(f"  Next Month/Day      = bytes {nxt[0]}/{nxt[1]}  (u16={_u16(nxt)})")
        print(f"  Next Time           = {nxt_time}  -> {PERIOD_NAMES.get(nxt_time, '??')}")
        print("  -- Social stats (2 bytes cada) --")
        for name, off in SOCIAL.items():
            print(f"  {name:<12} = {_u16(read(off, 2))}")

        print("\nConfira na tela do jogo: data, periodo, yen e os 5 social stats.")
    finally:
        kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
