"""Controlador central do Platinum Assistant.

Cola todas as pecas: config -> deteccao de save -> leitura (memoria) ->
estado persistente -> watcher -> DeepSeek -> missables. A GUI consome esta
camada via metodos simples e callbacks, sem conhecer os detalhes internos.

Fluxo tipico:
  controller = Controller()
  controller.start(on_update=lambda c: ...)   # inicia watcher + leitura inicial
  controller.refresh()                         # forca releitura + consulta API
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import guide_fetcher, memory_reader, save_reader, schedule
from .config_manager import load_config, resolve_save_location
from .deepseek_client import safe_query_guidance
from .file_watcher import SaveWatcher
from .process_utils import is_game_running
from .state_manager import StateManager


@dataclass
class Status:
    """Snapshot do estado de runtime para a GUI exibir."""

    game_running: bool = False
    save_found: bool = False
    save_message: str = ""
    backend: str = "-"
    last_error: Optional[str] = None
    online: bool = False  # ultima consulta a API teve sucesso
    querying: bool = False  # uma consulta a DeepSeek esta em andamento
    api_configured: bool = False  # ha uma chave no config
    upcoming: list[dict] = field(default_factory=list)
    guidance: dict = field(default_factory=dict)
    # Fonte primaria: passos do dia vindos do guia (lookup local, instantaneo).
    schedule_today: Optional[dict] = None
    schedule_period: list[dict] = field(default_factory=list)  # tarefas do periodo atual
    schedule_critical: list[dict] = field(default_factory=list)  # itens/infiltracoes do dia
    month_intro: str = ""


class Controller:
    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path) if config_path else load_config()
        self.state_mgr = StateManager()
        self.save_loc = resolve_save_location(self.config)
        self.status = Status(
            save_found=self.save_loc.found,
            save_message=self.save_loc.message,
        )
        self._watcher: Optional[SaveWatcher] = None
        # Poll de presenca do jogo: detecta P5R.exe abrindo/fechando sem depender
        # de um save acontecer (o watcher so reage a mudancas no save).
        self._presence_thread: Optional[threading.Thread] = None
        self._presence_stop = threading.Event()
        self._last_running: Optional[bool] = None
        # Assinatura (mes, dia, period_code) da ultima leitura ao vivo, usada para
        # detectar virada de dia OU de periodo (afternoon -> evening) e auto-atualizar.
        self._last_sig: Optional[tuple] = None
        self._on_update: Optional[Callable[["Controller"], None]] = None
        # Avisado logo antes de uma consulta lenta a DeepSeek comecar, para a GUI
        # poder mostrar "consultando..." em vez de um estado vazio.
        self._on_querying: Optional[Callable[["Controller"], None]] = None
        self._lock = threading.Lock()

    # ---- ciclo de vida -------------------------------------------------
    def start(self, on_update: Optional[Callable[["Controller"], None]] = None) -> None:
        self._on_update = on_update
        self.refresh()  # leitura inicial
        if self.save_loc.found and self.save_loc.savedata_dir and self.save_loc.slot_file:
            self._watcher = SaveWatcher(
                self.save_loc.savedata_dir,
                self.save_loc.slot_file,
                on_save=lambda _p: self.refresh(),
                poll_interval=float(self.config.get("poll_interval_seconds", 10)),
            )
            self._watcher.start()
            self.status.backend = self._watcher.backend
        # Inicia o monitor ao vivo (independe de save encontrado): detecta o jogo
        # abrindo/fechando E mudanca de data/periodo lendo a memoria periodicamente.
        self._last_running = self.status.game_running
        self._last_sig = self._read_live_sig()
        self._presence_stop.clear()
        self._presence_thread = threading.Thread(
            target=self._presence_loop, daemon=True
        )
        self._presence_thread.start()

    def _read_live_sig(self) -> Optional[tuple]:
        """Le uma assinatura leve (mes, dia, period_code) da memoria ao vivo.

        Retorna None se o jogo nao estiver rodando ou a leitura falhar (ex.: tela
        de loading/menu). Nao funde nada no estado — so serve para detectar mudanca.
        """
        proc_name = self.config.get("process_name", "P5R.exe")
        if not is_game_running(proc_name):
            return None
        try:
            live = memory_reader.read_live_date(proc_name)
            return (live.month, live.day, live.period_code)
        except Exception:
            return None

    def _presence_loop(self) -> None:
        """Monitor ao vivo: detecta o jogo abrindo/fechando E mudanca de data/periodo.

        Enquanto o jogo roda, le a data ao vivo (leve, sem fundir nada) a cada
        ciclo e dispara um refresh completo APENAS quando algo muda — virada de dia
        OU de periodo (ex.: afternoon -> evening) — alem de abrir/fechar o jogo.
        Leituras transitorias que falham (loading/menu) sao ignoradas para nao
        piscar a UI.
        """
        interval = max(1.0, float(self.config.get("date_poll_interval_seconds", 3)))
        while not self._presence_stop.wait(interval):
            running = is_game_running(self.config.get("process_name", "P5R.exe"))
            if running != self._last_running:
                # Jogo abriu ou fechou: refresh e (re)inicializa a assinatura.
                self._last_running = running
                self._last_sig = self._read_live_sig() if running else None
                self.refresh()
                continue
            if running:
                sig = self._read_live_sig()
                if sig is not None and sig != self._last_sig:
                    # Virou o dia ou o periodo: atualiza sozinho.
                    self._last_sig = sig
                    self.refresh()

    def stop(self) -> None:
        self._presence_stop.set()
        if self._presence_thread is not None:
            self._presence_thread.join(timeout=3)
            self._presence_thread = None
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ---- leitura + consulta -------------------------------------------
    def read_game_state(self) -> bool:
        """Le o estado do jogo e funde no estado persistente.

        Quando P5R.exe esta rodando, usa memoria viva read-only como fonte
        primaria para a data atual. Se a leitura de memoria falhar ou o jogo
        estiver fechado, cai para o save decriptado como fallback estavel.
        """
        self.status.game_running = is_game_running(
            self.config.get("process_name", "P5R.exe")
        )

        if self.status.game_running:
            try:
                partial = memory_reader.read_memory_state(
                    self.config.get("process_name", "P5R.exe")
                )
                self.state_mgr.apply_partial(partial, source="memory")
                self.state_mgr.save()
                self.status.last_error = None
                return True
            except memory_reader.MemoryReadError as exc:
                self.status.last_error = f"Memoria indisponivel, usando save: {exc}"
            except Exception as exc:
                self.status.last_error = f"Erro inesperado ao ler memoria: {exc}"

        path = None
        if self.save_loc.savedata_dir:
            path = save_reader.newest_save_slot(self.save_loc.savedata_dir)
        if path is None:
            path = self.save_loc.slot_file
        if path is None:
            return False
        try:
            partial = save_reader.read_save_state(path)
            if partial:
                self.state_mgr.apply_partial(partial, source="save")
                self.state_mgr.save()
                self.status.last_error = None
                return True
        except save_reader.SaveReadError as exc:
            self.status.last_error = str(exc)
        except Exception as exc:
            self.status.last_error = f"Erro inesperado ao ler o save: {exc}"
        return False

    def load_today_plan(self) -> None:
        """Fonte primaria: passos do dia vindos do guia (lookup local, instantaneo).

        Nao chama a API — preenche o Status com a entrada do cronograma para a
        data atual, as tarefas do periodo corrente e as tarefas criticas do dia.
        """
        date = self.state_mgr.state.get("in_game_date") or {}
        month, day, period = date.get("month"), date.get("day"), date.get("period")

        entry = schedule.day_entry(month, day)
        self.status.schedule_today = entry
        self.status.month_intro = schedule.month_intro(month)
        if entry:
            self.status.schedule_period = schedule.current_period_tasks(entry, period)
            self.status.schedule_critical = schedule.critical_tasks(entry)
        else:
            self.status.schedule_period = []
            self.status.schedule_critical = []

        # Missables hardcoded continuam como rede de seguranca complementar.
        self.status.upcoming = guide_fetcher.upcoming_missables(month, day)
        self.status.api_configured = bool(self.config.get("deepseek_api_key"))

    def ask_ai(self) -> None:
        """Consulta opcional a DeepSeek (sob demanda) com estado + contexto do guia."""
        date = self.state_mgr.state.get("in_game_date") or {}
        month, day = date.get("month"), date.get("day")
        guide_ctx = guide_fetcher.guide_context_for_date(month, day)

        self.status.api_configured = bool(self.config.get("deepseek_api_key"))

        # Avisa a GUI que uma consulta lenta (~13s) vai comecar.
        self.status.querying = True
        if self._on_querying is not None:
            try:
                self._on_querying(self)
            except Exception:
                pass

        guidance = safe_query_guidance(
            self.config, self.state_mgr.snapshot(), guide_ctx
        )
        self.status.querying = False
        if "_error" in guidance:
            self.status.online = False
            self.status.last_error = guidance["_error"]
        else:
            self.status.online = True
            self.state_mgr.cache_guidance(guidance)
            self.state_mgr.save()
        self.status.guidance = guidance

    def refresh(self) -> None:
        """Releitura da memoria + plano do dia (local, instantaneo) + notifica a GUI.

        NAO chama a DeepSeek: o guia e a fonte primaria. A IA fica sob demanda
        via ``ask_ai`` (botao na GUI), para nao impor a latencia de ~13s.
        """
        with self._lock:
            self.read_game_state()
            self.load_today_plan()
        # Mantem o poll de presenca em sincronia (evita refresh redundante).
        self._last_running = self.status.game_running
        if self._on_update is not None:
            try:
                self._on_update(self)
            except Exception:
                pass

    def query_ai_async(self) -> None:
        """Roda ask_ai() dentro do lock e notifica a GUI (chamado por um worker)."""
        with self._lock:
            self.ask_ai()
        if self._on_update is not None:
            try:
                self._on_update(self)
            except Exception:
                pass

    # ---- helpers para a GUI -------------------------------------------
    def tray_color(self) -> str:
        """verde=ok, amarelo=atencao, vermelho=missable iminente."""
        for m in self.status.upcoming:
            u = m.get("urgency_days")
            if u is not None and u <= 2 and m.get("type") == "missable":
                return "red"
        # Itens/infiltracoes de hoje (void 100% se perdidos) merecem atencao.
        if self.status.schedule_critical:
            return "yellow"
        if self.status.upcoming:
            return "yellow"
        if not self.status.game_running:
            return "yellow"
        return "green"
