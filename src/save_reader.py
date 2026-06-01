"""Leitor READ-ONLY do save do P5R (DATA.DAT) — fonte primaria e estavel.

Diferente da memoria viva (ASLR/heap), o save e um formato de arquivo: os
offsets dentro do bloco de dados sao fixos e sobrevivem a reiniciar/patch. Aqui
apenas DECRIPTAMOS e DESCOMPRIMIMOS o save em memoria para ler o estado do jogo
(data, stats, confidants, yen). NUNCA reempacotamos nem escrevemos no save.

Formato do DATA.DAT (PC), reimplementado a partir do algoritmo publico:
  0x00  4s   magic "DATA"
  0x04  I    file_crc (CRC-32/MPEG-2 sobre buffer[0x08:])
  0x08  I    timestamp
  0x0C  I    file_flags (bit31 = corpo cifrado em AES-256-CBC)
  0x10  16   IV
  0x20+      corpo (cifrado se bit31): header HHIIII + blocos
       0x20  H  header_size        0x22  H  header_size_comp
       0x24  I  data_size          0x28  I  data_size_comp
       0x2C  I  save_flags (bit0=header zlib, bit1=data zlib)
       0x30  I  data_crc (CRC-32/MPEG-2 sobre o data block descomprimido)
  bloco de dados do jogo (~0x30720 bytes) em data_offset, possivelmente zlib.

Chave AES (constante do jogo, mesma para todos os saves):
  base64 "3lOZS0kYSoOOtkC4c7IDfvNXnxIprUPTlUGVC3yBJF0="
"""

from __future__ import annotations

import base64
import datetime
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config_manager import PROJECT_ROOT

SAVE_OFFSETS_PATH = PROJECT_ROOT / "save_offsets.json"

# Codigo do campo 'time' do header -> nome do periodo do dia (P5R).
# 0/3/4/5 observados ao vivo; intermediarios inferidos pela ordem canonica.
PERIOD_NAMES = {
    0: "early morning",
    1: "morning",
    2: "lunchtime",
    3: "afternoon",
    4: "after school",
    5: "evening",
    6: "night",
}

try:
    from Crypto.Cipher import AES  # pycryptodome
    _AES_OK = True
except Exception:
    AES = None  # type: ignore
    _AES_OK = False

CRYPT_KEY = base64.b64decode(b"3lOZS0kYSoOOtkC4c7IDfvNXnxIprUPTlUGVC3yBJF0=")


# ---- CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xffffffff, sem reflexao) --------
def _build_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        table.append(c)
    return table


_CRC_TABLE = _build_crc_table()


def calc_crc(buffer: bytes, init: int = 0xFFFFFFFF) -> int:
    for b in buffer:
        init = ((init << 8) ^ _CRC_TABLE[b ^ (init >> 24)]) & 0xFFFFFFFF
    return init


def _align(v: int, a: int) -> int:
    return (v + (a - 1)) & ~(a - 1)


class SaveReadError(Exception):
    pass


@dataclass
class DecodedSave:
    data: bytes               # bloco de dados do jogo (descomprimido)
    header: bytes             # bloco de header (descomprimido) — metadados
    timestamp: int
    file_crc_ok: bool
    data_crc_ok: bool
    # campos uteis do header (metadados de save)
    playtime: int = 0
    day_counter: int = 0      # contador de dias desde o inicio (nao e mes/dia)
    period_code: int = 0      # campo 'time' do header = periodo do dia
    level: int = 0


def decode_save(path: str | Path) -> DecodedSave:
    """Decripta + descomprime o DATA.DAT e devolve o bloco de dados do jogo.

    Read-only: nada e escrito no save. Levanta SaveReadError em falha.
    """
    if not _AES_OK:
        raise SaveReadError(
            "pycryptodome ausente. Rode: pip install pycryptodome"
        )
    p = Path(path)
    try:
        buffer = p.read_bytes()
    except OSError as exc:
        raise SaveReadError(f"Nao foi possivel ler {p}: {exc}") from exc

    if len(buffer) < 0x40 or buffer[:4] != b"DATA":
        raise SaveReadError("Arquivo nao parece um save PC do P5R (magic != DATA).")

    file_crc, _timestamp, file_flags = struct.unpack("<3I", buffer[0x04:0x10])
    file_iv = buffer[0x10:0x20]
    file_crc_ok = calc_crc(buffer[0x08:]) == file_crc

    body = buffer
    if file_flags >> 31:
        try:
            cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, file_iv)
            body = buffer[:0x20] + cipher.decrypt(buffer[0x20:])
        except Exception as exc:
            raise SaveReadError(f"Falha ao decriptar (AES): {exc}") from exc

    header_size, header_size_comp, data_size, data_size_comp, save_flags, data_crc = \
        struct.unpack("<HHIIII", body[0x20:0x34])

    # Bloco de header (metadados). Comprimido se save_flags bit0.
    if save_flags & 1:
        header = zlib.decompress(body[0x40:header_size_comp])
        data_offset = header_size_comp
    else:
        header = body[0x40:header_size]
        data_offset = header_size
    data_offset = _align(data_offset, 16)

    # Bloco de dados do jogo. Comprimido se save_flags bit1.
    if save_flags & 2:
        try:
            data = zlib.decompress(body[data_offset:data_offset + data_size_comp])
        except Exception as exc:
            raise SaveReadError(f"Falha ao descomprimir o data block: {exc}") from exc
    else:
        data = body[data_offset:data_offset + data_size]

    data_crc_ok = calc_crc(data) == data_crc

    # Campos do header (layout <IHBBBBxBBBBB64s64s256s): playtime, day, time, ...
    playtime = day_counter = period_code = level = 0
    if len(header) >= 0xA:
        playtime, day_counter, period_code, _pt, _diff, level = struct.unpack(
            "<IHBBBB", header[:0xA]
        )

    return DecodedSave(
        data=data, header=header, timestamp=_timestamp,
        file_crc_ok=file_crc_ok, data_crc_ok=data_crc_ok,
        playtime=playtime, day_counter=day_counter,
        period_code=period_code, level=level,
    )


# ---- estado de jogo a partir do save (fonte primaria) ----------------------
_OFFSETS_CACHE: Optional[dict] = None


def load_save_offsets(path: str | Path = SAVE_OFFSETS_PATH) -> dict:
    global _OFFSETS_CACHE
    if _OFFSETS_CACHE is None:
        with Path(path).open("r", encoding="utf-8") as fh:
            _OFFSETS_CACHE = json.load(fh)
    return _OFFSETS_CACHE


_TYPE_FMT = {"u8": "<B", "u16": "<H", "u32": "<I"}


def _read_field(data: bytes, spec: dict) -> int:
    off = int(spec["offset"], 16) if isinstance(spec["offset"], str) else int(spec["offset"])
    fmt = _TYPE_FMT[spec.get("type", "u8")]
    return struct.unpack_from(fmt, data, off)[0]


def read_save_state(path: str | Path) -> dict[str, Any]:
    """Le o save e devolve um fragmento no formato do session_state.

    Hoje extrai a data in-game (mes/dia do data block + periodo do header). Stats
    e confidants entram aqui conforme forem mapeados em save_offsets.json.
    Retorna {} se a data lida for invalida (save corrompido/incompleto).
    """
    dec = decode_save(path)
    if not dec.data_crc_ok:
        # Save possivelmente sendo escrito; ignora pra nao ler lixo.
        return {}
    offs = load_save_offsets()
    db = offs.get("data_block", {})
    month = _read_field(dec.data, db["month"])
    day = _read_field(dec.data, db["day"])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return {}
    period = PERIOD_NAMES.get(dec.period_code, None)
    return {"in_game_date": {"month": month, "day": day, "period": period}}


def newest_save_slot(savedata_dir: str | Path) -> Optional[Path]:
    """Retorna o DATA.DAT modificado mais recentemente entre os slots (slot ativo)."""
    saves = list(Path(savedata_dir).glob("*/DATA.DAT"))
    if not saves:
        return None
    return max(saves, key=lambda p: p.stat().st_mtime)


# ---- CLI utilitaria (inspecao / mapeamento de offsets) ---------------------
def _cli(argv: list[str]) -> int:
    if not argv:
        print("Uso: python -m src.save_reader <dump|find> <DATA.DAT> [args]")
        return 1
    cmd, path = argv[0], argv[1]
    dec = decode_save(path)
    print(f"file_crc_ok={dec.file_crc_ok} data_crc_ok={dec.data_crc_ok} "
          f"data_len=0x{len(dec.data):X} header_len=0x{len(dec.header):X}")
    print(f"playtime={dec.playtime}s day_counter={dec.day_counter} level={dec.level}")

    if cmd == "find" and len(argv) >= 3:
        # find <DATA.DAT> <tipo> <valor> -> offsets do data block que batem
        type_name, value = argv[2], int(argv[3])
        fmt = {"u8": "<B", "u16": "<H", "u32": "<I"}[type_name]
        needle = struct.pack(fmt, value)
        data = dec.data
        hits = []
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            hits.append(i)
            start = i + 1
        print(f"{len(hits)} ocorrencias de {type_name}={value} no data block:")
        for h in hits[:60]:
            print(f"   data+0x{h:X}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
