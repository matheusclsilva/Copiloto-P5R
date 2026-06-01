"""Monitoramento do save com watchdog (item 4).

Observa o diretorio ``savedata`` e dispara um callback quando o jogo escreve no
slot ativo (DATA.DAT). Inclui debounce, pois o jogo costuma emitir varios eventos
de escrita em rapida sucessao ao salvar.

Tambem oferece um fallback por polling (mtime) caso o watchdog nao esteja
disponivel ou o sistema de arquivos nao emita eventos confiaveis.

Nada aqui escreve no save — apenas observa.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore

    _WATCHDOG_AVAILABLE = True
except Exception:
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _WATCHDOG_AVAILABLE = False


DEFAULT_DEBOUNCE_SECONDS = 2.0


class _SaveEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    """Handler interno que filtra eventos relevantes e aplica debounce."""

    def __init__(self, target_file: Path, callback: Callable[[Path], None],
                 debounce: float = DEFAULT_DEBOUNCE_SECONDS):
        super().__init__()
        self.target_file = target_file.resolve()
        self.target_name = self.target_file.name.lower()
        self.callback = callback
        self.debounce = debounce
        self._last_fire = 0.0
        self._lock = threading.Lock()

    def _maybe_fire(self, path_str: str) -> None:
        p = Path(path_str)
        # Aceita eventos do arquivo alvo OU de qualquer DATA.DAT (slot pode variar).
        if p.name.lower() != self.target_name:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_fire < self.debounce:
                return
            self._last_fire = now
        try:
            self.callback(p)
        except Exception:
            pass  # callback nunca deve derrubar o observer

    def on_modified(self, event):  # noqa: D401
        if not event.is_directory:
            self._maybe_fire(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_fire(event.src_path)

    def on_moved(self, event):
        dest = getattr(event, "dest_path", None)
        if dest:
            self._maybe_fire(dest)


class SaveWatcher:
    """Observa o diretorio do save e chama ``on_save`` quando o slot muda."""

    def __init__(self, savedata_dir: Path, slot_file: Path,
                 on_save: Callable[[Path], None],
                 debounce: float = DEFAULT_DEBOUNCE_SECONDS,
                 poll_interval: float = 5.0):
        self.savedata_dir = Path(savedata_dir)
        self.slot_file = Path(slot_file)
        self.on_save = on_save
        self.debounce = debounce
        self.poll_interval = poll_interval

        self._observer = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Acompanha o mtime de QUALQUER slot (DATA01, DATA02, ...), nao so um
        # arquivo fixo: o jogador pode trocar de slot e o slot ativo passa a ser
        # o DATA.DAT modificado mais recentemente na pasta savedata.
        self._last_mtime: Optional[float] = None

    @property
    def backend(self) -> str:
        return "watchdog" if _WATCHDOG_AVAILABLE else "polling"

    def start(self) -> None:
        if _WATCHDOG_AVAILABLE and self.savedata_dir.is_dir():
            handler = _SaveEventHandler(self.slot_file, self._fire, self.debounce)
            self._observer = Observer()
            # recursive=True pois cada slot fica em subpasta (DATA01/DATA.DAT).
            self._observer.schedule(handler, str(self.savedata_dir), recursive=True)
            self._observer.start()
        else:
            self._start_polling()

    def _start_polling(self) -> None:
        self._last_mtime = self._current_mtime()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _current_mtime(self) -> Optional[float]:
        """mtime do DATA.DAT mais recente entre TODOS os slots da savedata.

        Em vez de olhar so ``slot_file``, varre ``savedata/*/DATA.DAT`` para que
        trocar de slot (DATA01 -> DATA02) ainda dispare o callback.
        """
        newest = self._newest_slot()
        if newest is None:
            return None
        try:
            return newest.stat().st_mtime
        except OSError:
            return None

    def _newest_slot(self) -> Optional[Path]:
        """Retorna o DATA.DAT modificado mais recentemente (slot ativo)."""
        try:
            saves = list(self.savedata_dir.glob("*/DATA.DAT"))
        except OSError:
            saves = []
        if not saves:
            # Fallback: o slot fixo informado na construcao.
            return self.slot_file if self.slot_file.is_file() else None
        try:
            return max(saves, key=lambda p: p.stat().st_mtime)
        except (OSError, ValueError):
            return None

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            mtime = self._current_mtime()
            if mtime is not None and mtime != self._last_mtime:
                self._last_mtime = mtime
                # Dispara com o slot ativo do momento, nao com um slot fixo.
                self._fire(self._newest_slot() or self.slot_file)

    def _fire(self, path: Path) -> None:
        try:
            self.on_save(path)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3)
            self._poll_thread = None
