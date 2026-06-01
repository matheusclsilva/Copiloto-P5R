"""Read-only live memory reader for Persona 5 Royal.

The game stores the current calendar as a day counter in live memory. The
stable entry point found for this install/build is:

    [P5R.exe+286C188] + 0 = u16 day_counter
    [P5R.exe+286C188] + 2 = u8  period_code

The in-game date is derived from the save header convention:
    date = 2016-04-09 + (day_counter - 8) days
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime as _dt
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import psutil  # type: ignore

    _PSUTIL_AVAILABLE = True
except Exception:
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False

from .save_reader import PERIOD_NAMES

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

DAY_POINTER_OFFSET = 0x286C188
BASE_DATE = _dt.date(2016, 4, 9)
BASE_COUNTER = 8

# --- CheatTable v30: base estatica de progresso (endereco DIRETO, sem deref) ---
# "Static Address Base (Money)[Steam]" = P5R.exe+0x28551FC. Os campos sao lidos
# como base + offset. Validado ao vivo: yen confere e periodo (+0x7052) bate com
# o ponteiro de data; social stats sao PONTOS INTERNOS (nao o rank 1-5 exibido).
CT_BASE_RVA = 0x28551FC
OFF_YEN = 0x0
SOCIAL_OFFSETS = {
    "knowledge": 0x101E0,
    "charm": 0x101E2,
    "proficiency": 0x101E4,
    "guts": 0x101E6,
    "kindness": 0x101E8,
}

# Array de confidants (CT v30, grupo "Confidants" = base + 0x1005E, direto).
# 23 slots de 16 bytes: +0 = id do confidant (u16), +2 = rank/Level (u16), +4 = points.
# O proprio id diz quem e (dropdown [list] Confidants), entao nao precisa adivinhar.
OFF_CONFIDANTS = 0x1005E
CONFIDANT_SLOT_COUNT = 23
CONFIDANT_SLOT_STRIDE = 0x10
CONFIDANT_RANK_OFF = 0x2

# id do CT -> nome canonico usado no state_manager (CONFIDANT_NAMES). Inclui as
# variantes 💔 (romance rompido) e a forma Sumire, todas mapeadas para o mesmo nome.
CONFIDANT_ID_NAMES = {
    2: "Morgana",
    3: "Makoto", 23: "Makoto",
    4: "Haru", 24: "Haru",
    5: "Yusuke",
    6: "Sojiro",
    7: "Ann", 25: "Ann",
    8: "Ryuji",
    9: "Akechi",
    10: "Futaba", 26: "Futaba",
    11: "Chihaya", 27: "Chihaya",
    13: "Iwai",
    14: "Takemi", 28: "Takemi",
    15: "Kawakami", 29: "Kawakami",
    16: "Ohya", 30: "Ohya",
    17: "Shinya",
    18: "Hifumi", 31: "Hifumi",
    19: "Mishima",
    20: "Toranosuke",
    21: "Sae",
    33: "Kasumi", 34: "Kasumi", 36: "Kasumi", 37: "Kasumi",
    35: "Maruki",
}

# Limiares de pontos internos -> rank exibido (1..5) no P5R. Cada lista e o total
# acumulado de pontos para ATINGIR rank 2, 3, 4 e 5. O ultimo valor (rank 5) bate
# exatamente com o "max" rotulado no CheatTable, e os pontos atuais (todos rank 1)
# foram confirmados ao vivo. Se algum rank intermediario divergir do jogo, ajuste
# aqui.
STAT_RANK_THRESHOLDS = {
    "knowledge": [41, 86, 131, 192],
    "charm": [26, 58, 90, 132],
    "guts": [23, 47, 80, 113],
    "kindness": [28, 64, 100, 136],
    "proficiency": [14, 33, 58, 87],
}


def points_to_rank(stat: str, points: int) -> int:
    """Converte pontos internos de um social stat no rank exibido (1..5)."""
    rank = 1
    for threshold in STAT_RANK_THRESHOLDS.get(stat, []):
        if points >= threshold:
            rank += 1
    return min(rank, 5)


class MemoryReadError(Exception):
    pass


@dataclass(frozen=True)
class LiveDate:
    month: int
    day: int
    period: Optional[str]
    day_counter: int
    period_code: int
    module_base: int
    pointer_address: int
    target_address: int


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


def _find_process(process_name: str) -> Any:
    if not _PSUTIL_AVAILABLE:
        raise MemoryReadError("psutil ausente; nao foi possivel localizar o processo.")
    target = process_name.lower()
    for proc in psutil.process_iter(["pid", "name"]):  # type: ignore[union-attr]
        if (proc.info.get("name") or "").lower() == target:
            return proc
    raise MemoryReadError(f"{process_name} nao esta rodando.")


def _parse_map_base(addr: str) -> int:
    # psutil on Windows returns strings like "0x140000000".
    first = addr.split("-", 1)[0]
    return int(first, 16)


def _module_base(proc: Any, module_name: str) -> int:
    module_name = module_name.lower()
    candidates: list[int] = []
    try:
        maps = proc.memory_maps(grouped=False)
    except Exception as exc:
        raise MemoryReadError(f"Nao foi possivel listar modulos do processo: {exc}") from exc

    for mapping in maps:
        path = getattr(mapping, "path", "") or ""
        if Path(path).name.lower() == module_name:
            candidates.append(_parse_map_base(mapping.addr))
    if not candidates:
        raise MemoryReadError(f"Modulo {module_name} nao encontrado no processo.")
    return min(candidates)


def _read_process_memory(handle: int, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(read),
    )
    if not ok or read.value != size:
        error = ctypes.get_last_error()
        raise MemoryReadError(
            f"Falha ao ler memoria em 0x{address:X} ({read.value}/{size} bytes, erro {error})."
        )
    return buffer.raw


def _date_from_counter(day_counter: int) -> _dt.date:
    return BASE_DATE + _dt.timedelta(days=day_counter - BASE_COUNTER)


def read_live_date(process_name: str = "P5R.exe") -> LiveDate:
    """Read the current in-game date from P5R memory without writing anything."""
    proc = _find_process(process_name)
    module_base = _module_base(proc, process_name)
    pointer_address = module_base + DAY_POINTER_OFFSET

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        False,
        int(proc.info["pid"]),
    )
    if not handle:
        raise MemoryReadError(f"OpenProcess falhou: erro {ctypes.get_last_error()}.")

    try:
        target_address = struct.unpack("<Q", _read_process_memory(handle, pointer_address, 8))[0]
        if target_address == 0:
            raise MemoryReadError("Ponteiro de data retornou NULL.")

        raw = _read_process_memory(handle, target_address, 4)
        day_counter = struct.unpack("<H", raw[:2])[0]
        period_code = raw[2]

        # P5R playable calendar is roughly Apr-Feb; keep this broad for cutscenes
        # and postgame-like edge cases while rejecting obvious pointer garbage.
        if not (0 <= day_counter <= 400):
            raise MemoryReadError(f"day_counter invalido lido da memoria: {day_counter}.")
        if period_code not in PERIOD_NAMES:
            raise MemoryReadError(f"period_code invalido lido da memoria: {period_code}.")

        date = _date_from_counter(day_counter)
        return LiveDate(
            month=date.month,
            day=date.day,
            period=PERIOD_NAMES.get(period_code),
            day_counter=day_counter,
            period_code=period_code,
            module_base=module_base,
            pointer_address=pointer_address,
            target_address=target_address,
        )
    finally:
        kernel32.CloseHandle(handle)


def _read_progress_extras(process_name: str, module_base: int) -> dict[str, Any]:
    """Le yen e social stats da base estatica do CheatTable v30 (read-only).

    Retorna {} se a leitura falhar (ex.: tela de titulo/menu sem save). A data ja
    foi validada por ``read_live_date`` antes desta chamada, entao aqui assumimos
    gameplay; ainda assim toleramos falhas para nunca derrubar a leitura da data.
    """
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, _pid_of(process_name)
    )
    if not handle:
        return {}
    try:
        base = module_base + CT_BASE_RVA
        yen = struct.unpack("<I", _read_process_memory(handle, base + OFF_YEN, 4))[0]
        stats: dict[str, int] = {}
        for name, off in SOCIAL_OFFSETS.items():
            points = struct.unpack("<H", _read_process_memory(handle, base + off, 2))[0]
            stats[name] = points_to_rank(name, points)

        confidants = _read_confidants(handle, base)
        result: dict[str, Any] = {"yen": yen, "social_stats": stats}
        if confidants:
            result["confidants"] = confidants
        return result
    except MemoryReadError:
        return {}
    finally:
        kernel32.CloseHandle(handle)


def _read_confidants(handle: int, base: int) -> dict[str, int]:
    """Le o array de confidants (id + rank) da base estatica do CT v30.

    Retorna {nome: rank}. So inclui confidants cujo id e reconhecido e com rank
    valido (1..10). Slots vazios (id 0 / rank 0) sao ignorados. Tolerante a falha
    de leitura por slot (nunca derruba o resto).
    """
    block = base + OFF_CONFIDANTS
    out: dict[str, int] = {}
    for i in range(CONFIDANT_SLOT_COUNT):
        slot = block + i * CONFIDANT_SLOT_STRIDE
        try:
            cid = struct.unpack("<H", _read_process_memory(handle, slot, 2))[0]
            rank = struct.unpack(
                "<H", _read_process_memory(handle, slot + CONFIDANT_RANK_OFF, 2)
            )[0]
        except MemoryReadError:
            continue
        name = CONFIDANT_ID_NAMES.get(cid)
        if name and 1 <= rank <= 10:
            # Se o mesmo confidant aparecer em dois slots (ex.: variante 💔),
            # mantem o maior rank.
            out[name] = max(out.get(name, 0), rank)
    return out


def _pid_of(process_name: str) -> int:
    return int(_find_process(process_name).info["pid"])


def read_memory_state(process_name: str = "P5R.exe") -> dict[str, Any]:
    live = read_live_date(process_name)
    state: dict[str, Any] = {
        "in_game_date": {
            "month": live.month,
            "day": live.day,
            "period": live.period,
        }
    }
    # Yen e social stats vem da base estatica do CheatTable v30. Se falhar, a data
    # sozinha ja e suficiente — nao propaga erro.
    state.update(_read_progress_extras(process_name, live.module_base))
    return state
