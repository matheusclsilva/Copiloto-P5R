"""Read-only probe for locating the P5R live save-state block in memory.

This does not write to the game process. It decodes the latest local DATA.DAT,
builds byte signatures around the known save date offsets, and searches readable
regions of P5R.exe for those signatures.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import save_reader  # noqa: E402
from src.config_manager import load_config, resolve_save_location  # noqa: E402


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.VirtualQueryEx.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wt.BOOL


def find_pid(process_name: str = "P5R.exe") -> int:
    import psutil

    wanted = process_name.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        if (proc.info.get("name") or "").lower() == wanted:
            return int(proc.info["pid"])
    raise SystemExit(f"{process_name} is not running")


def readable_regions(handle: int):
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    max_address = 0x7FFFFFFFFFFF
    while address < max_address:
        result = kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not result:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        protect = int(mbi.Protect)
        if (
            int(mbi.State) == MEM_COMMIT
            and size > 0
            and not (protect & PAGE_NOACCESS)
            and not (protect & PAGE_GUARD)
        ):
            yield base, size, protect, int(mbi.Type)
        address = base + size


def read_region(handle: int, base: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(base),
        buffer,
        size,
        ctypes.byref(read),
    )
    if not ok or read.value == 0:
        return b""
    return buffer.raw[: read.value]


def read_at(handle: int, address: int, size: int) -> bytes:
    return read_region(handle, address, size)


def all_hits(haystack: bytes, needle: bytes):
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            return
        yield idx
        start = idx + 1


def latest_save_path() -> Path:
    loc = resolve_save_location(load_config())
    if loc.savedata_dir:
        newest = save_reader.newest_save_slot(loc.savedata_dir)
        if newest:
            return newest
    if loc.slot_file:
        return loc.slot_file
    raise SystemExit(f"Save not found: {loc.message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--value-scan",
        action="store_true",
        help="Also scan common date value layouts when the save-block signature is absent.",
    )
    parser.add_argument("--max-value-hits", type=int, default=250)
    parser.add_argument(
        "--save-path",
        type=Path,
        help="Use a specific DATA.DAT instead of the newest save slot.",
    )
    parser.add_argument(
        "--watch",
        nargs="*",
        help="Watch explicit addresses, e.g. 0x4593ee 0x46b1ae.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Write all date-like candidate addresses and current raw values to this JSON file.",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="Compare an earlier snapshot against current values at the same addresses.",
    )
    args = parser.parse_args()

    save_path = args.save_path or latest_save_path()
    decoded = save_reader.decode_save(save_path)
    offsets = save_reader.load_save_offsets()
    date_off = int(offsets["data_block"]["month"]["offset"], 16)
    month = decoded.data[date_off]
    day = decoded.data[date_off + 1]

    signatures = []
    for before, after in [(16, 16), (32, 32), (64, 64), (128, 128)]:
        start = date_off - before
        end = date_off + 2 + after
        if start >= 0 and end <= len(decoded.data):
            signatures.append((f"data_date_-{before}/+{after}", date_off - before, before, decoded.data[start:end]))

    header_signatures = [
        ("header_10", 0, 6, decoded.header[:10]),
        ("header_16", 0, 6, decoded.header[:16]),
        ("header_32", 0, 6, decoded.header[:32]),
        (
            "header_day_period_level",
            4,
            0,
            struct.pack("<HBBBB", decoded.day_counter, decoded.period_code, decoded.header[7], decoded.header[8], decoded.level),
        ),
        (
            "u32_day_period",
            0,
            0,
            struct.pack("<II", decoded.day_counter, decoded.period_code),
        ),
        (
            "u16_day_u8_period",
            0,
            0,
            struct.pack("<HB", decoded.day_counter, decoded.period_code),
        ),
    ]
    signatures.extend(header_signatures)

    pid = find_pid()
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    hits = []
    value_hits = []
    value_counts = {}
    value_samples = {}
    snapshot_candidates = []
    value_patterns = [
        ("u8_month_day", bytes([month, day]), 0),
        ("u8_day_month", bytes([day, month]), 0),
        ("u16_month_day", struct.pack("<HH", month, day), 0),
        ("u32_month_day", struct.pack("<II", month, day), 0),
        ("u16_day_counter", struct.pack("<H", decoded.day_counter), 0),
        ("u32_day_counter", struct.pack("<I", decoded.day_counter), 0),
    ]
    regions_scanned = 0
    bytes_scanned = 0
    try:
        if args.compare:
            previous = json.loads(args.compare.read_text(encoding="utf-8"))
            expected = {
                "month": month,
                "day": day,
                "day_counter": decoded.day_counter,
                "period": decoded.period_code,
            }
            matches = []
            changed = 0
            for candidate in previous.get("candidates", []):
                address = int(candidate["address"])
                raw = read_at(handle, address, 24)
                if not raw:
                    continue
                raw_hex = raw.hex(" ")
                if raw_hex != candidate.get("raw", ""):
                    changed += 1
                pattern = candidate["pattern"]
                ok = False
                decoded_now = {}
                if pattern == "u8_month_day" and len(raw) >= 2:
                    decoded_now = {"month": raw[0], "day": raw[1]}
                    ok = decoded_now == {"month": month, "day": day}
                elif pattern == "u16_month_day" and len(raw) >= 4:
                    m, d = struct.unpack("<HH", raw[:4])
                    decoded_now = {"month": m, "day": d}
                    ok = decoded_now == {"month": month, "day": day}
                elif pattern == "u32_month_day" and len(raw) >= 8:
                    m, d = struct.unpack("<II", raw[:8])
                    decoded_now = {"month": m, "day": d}
                    ok = decoded_now == {"month": month, "day": day}
                elif pattern == "u16_day_u8_period" and len(raw) >= 3:
                    dc = struct.unpack("<H", raw[:2])[0]
                    decoded_now = {"day_counter": dc, "period": raw[2]}
                    ok = decoded_now == {"day_counter": decoded.day_counter, "period": decoded.period_code}
                elif pattern == "u32_day_period" and len(raw) >= 8:
                    dc, period_now = struct.unpack("<II", raw[:8])
                    decoded_now = {"day_counter": dc, "period": period_now}
                    ok = decoded_now == {"day_counter": decoded.day_counter, "period": decoded.period_code}
                if ok and raw_hex != candidate.get("raw", ""):
                    matches.append(
                        {
                            "pattern": pattern,
                            "address": candidate["address_hex"],
                            "previous_raw": candidate.get("raw"),
                            "current_raw": raw_hex,
                            "decoded": decoded_now,
                            "region_base": candidate.get("region_base"),
                            "protect": candidate.get("protect"),
                            "type": candidate.get("type"),
                        }
                    )
            print(
                json.dumps(
                    {
                        "pid": pid,
                        "save_path": str(save_path),
                        "expected_from_latest_save": expected,
                        "previous_save_date": previous.get("save_date"),
                        "previous_day_counter": previous.get("save_day_counter"),
                        "previous_period_code": previous.get("save_period_code"),
                        "candidates_checked": len(previous.get("candidates", [])),
                        "changed_candidates": changed,
                        "matching_changed_candidates": matches[:100],
                        "matching_changed_count": len(matches),
                    },
                    indent=2,
                )
            )
            return 0

        if args.watch is not None:
            import time

            addresses = [int(v, 16) if v.lower().startswith("0x") else int(v) for v in args.watch]
            print("Watching addresses as <u8,u8,u16,u16,u32,u32>. Press Ctrl+C to stop.")
            while True:
                row = {}
                for address in addresses:
                    raw = read_at(handle, address, 16)
                    if len(raw) >= 8:
                        row[hex(address)] = {
                            "hex": raw.hex(" "),
                            "u8": list(raw[:4]),
                            "u16": list(struct.unpack("<HHHH", raw[:8])),
                            "u32": list(struct.unpack("<II", raw[:8])),
                        }
                    else:
                        row[hex(address)] = {"error": "unreadable"}
                print(json.dumps(row, ensure_ascii=False), flush=True)
                time.sleep(1.0)

        for base, size, protect, mem_type in readable_regions(handle):
            regions_scanned += 1
            # Keep scans responsive; the save-state block is small and should be
            # in ordinary committed memory, not multi-GB mapped ranges.
            if size > 256 * 1024 * 1024:
                continue
            data = read_region(handle, base, size)
            bytes_scanned += len(data)
            if not data:
                continue
            for sig_name, sig_anchor_offset, date_rel, sig in signatures:
                for idx in all_hits(data, sig):
                    block_base = base + idx - sig_anchor_offset
                    month_addr = block_base + date_off
                    hits.append(
                        {
                            "signature": sig_name,
                            "match_address": hex(base + idx),
                            "candidate_block_base": hex(block_base),
                            "month_address": hex(month_addr),
                            "day_address": hex(month_addr + 1),
                            "observed_around_match": data[max(0, idx - 16) : idx + len(sig) + 16].hex(" "),
                        }
                    )
            if args.value_scan and len(value_hits) < args.max_value_hits:
                for name, pattern, rel in value_patterns:
                    for idx in all_hits(data, pattern):
                        value_hits.append(
                            {
                                "pattern": name,
                                "address": hex(base + idx + rel),
                                "region_base": hex(base),
                                "region_size": hex(size),
                                "protect": hex(protect),
                                "type": hex(mem_type),
                                "nearby": data[max(0, idx - 16) : idx + len(pattern) + 16].hex(" "),
                            }
                        )
                        if len(value_hits) >= args.max_value_hits:
                            break
                    if len(value_hits) >= args.max_value_hits:
                        break
            if args.value_scan:
                for name, pattern, rel in value_patterns:
                    count = 0
                    sample_bucket = value_samples.setdefault(name, [])
                    for idx in all_hits(data, pattern):
                        count += 1
                        if len(sample_bucket) < 10:
                            sample_bucket.append(
                                {
                                    "address": hex(base + idx + rel),
                                    "region_base": hex(base),
                                    "region_size": hex(size),
                                    "protect": hex(protect),
                                    "type": hex(mem_type),
                                    "nearby": data[
                                        max(0, idx - 16) : idx + len(pattern) + 16
                                    ].hex(" "),
                                }
                            )
                    value_counts[name] = value_counts.get(name, 0) + count
            if args.snapshot:
                snapshot_patterns = [
                    ("u8_month_day", bytes([month, day]), 0, 8),
                    ("u16_month_day", struct.pack("<HH", month, day), 0, 16),
                    ("u32_month_day", struct.pack("<II", month, day), 0, 24),
                    ("u16_day_u8_period", struct.pack("<HB", decoded.day_counter, decoded.period_code), 0, 16),
                    ("u32_day_period", struct.pack("<II", decoded.day_counter, decoded.period_code), 0, 24),
                ]
                for name, pattern, rel, read_len in snapshot_patterns:
                    for idx in all_hits(data, pattern):
                        address = base + idx + rel
                        raw = data[idx + rel : idx + rel + read_len]
                        snapshot_candidates.append(
                            {
                                "pattern": name,
                                "address": address,
                                "address_hex": hex(address),
                                "raw": raw.hex(" "),
                                "region_base": hex(base),
                                "region_size": hex(size),
                                "protect": hex(protect),
                                "type": hex(mem_type),
                            }
                        )
    finally:
        kernel32.CloseHandle(handle)

    result = {
        "pid": pid,
        "save_path": str(save_path),
        "save_date": {"month": month, "day": day},
        "save_day_counter": decoded.day_counter,
        "save_period_code": decoded.period_code,
        "data_offset_month": hex(date_off),
        "regions_scanned": regions_scanned,
        "bytes_scanned": bytes_scanned,
        "hits": hits[:50],
        "hit_count": len(hits),
        "value_hits": value_hits,
        "value_hit_count_returned": len(value_hits),
        "value_counts": value_counts,
        "value_samples": value_samples,
    }
    if args.snapshot:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot_doc = {
            **result,
            "candidate_count": len(snapshot_candidates),
            "candidates": snapshot_candidates,
        }
        args.snapshot.write_text(json.dumps(snapshot_doc, indent=2), encoding="utf-8")
        result["snapshot"] = str(args.snapshot)
        result["snapshot_candidate_count"] = len(snapshot_candidates)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
