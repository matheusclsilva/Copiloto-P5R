"""Interface grafica do Platinum Assistant (item 6) — PyQt6.

Janela flutuante compacta (~400x320), sempre no topo, com:
  - cabecalho (data in-game + horario do ultimo save)
  - bloco "AGORA" (next_action da API)
  - bloco de avisos (missables proximos)
  - linha de social stats
  - botoes: Atualizar / Ver Plano do Dia / Config

Minimiza para a system tray com icone colorido (verde/amarelo/vermelho).
A leitura/consulta roda em thread separada para nao travar a UI.
"""

from __future__ import annotations

from html import escape
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPixmap
import ctypes
from ctypes import wintypes
import threading

def _native_hotkey_thread(signal_emitter):
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x0000, 0x78):
        print("Aviso: Não foi possível registrar o atalho F9.")
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            signal_emitter.toggle.emit()
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config_manager import save_config
from .controller import Controller

_COLOR_HEX = {"green": "#2ecc71", "yellow": "#f1c40f", "red": "#e74c3c"}

APP_STYLE = """
QWidget {
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 10.5pt;
    color: #202124;
    background-color: #f4f6f8;
}
QLabel {
    background-color: transparent;
    color: #202124;
    line-height: 135%;
}
QPushButton {
    background-color: #ffffff;
    color: #202124;
    border: 1px solid #b7c0ca;
    border-radius: 4px;
    font-weight: 600;
    min-height: 30px;
    padding: 5px 10px;
}
QPushButton:hover {
    background-color: #eef3f8;
    border-color: #8794a3;
}
QPushButton:disabled {
    background-color: #e5e9ef;
    color: #7a8490;
}
QLineEdit, QSpinBox, QComboBox {
    background-color: #ffffff;
    color: #202124;
    border: 1px solid #b7c0ca;
    border-radius: 4px;
    min-height: 28px;
    padding: 3px 6px;
}
QTextEdit {
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 10.5pt;
    background-color: #ffffff;
    color: #202124;
    border: 1px solid #c8d0d8;
}
QScrollArea {
    background-color: #f4f6f8;
    border: 1px solid #d3d9df;
}
QFrame {
    color: #c8d0d8;
}
"""


def _color_icon(color: str) -> QIcon:
    pix = QPixmap(16, 16)
    pix.fill(QColor(_COLOR_HEX.get(color, "#888888")))
    return QIcon(pix)


class RefreshWorker(QThread):
    """Roda controller.refresh() fora da thread de UI (rapido: lookup local)."""

    done = pyqtSignal()

    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller

    def run(self) -> None:
        self.controller.refresh()
        self.done.emit()


class AiWorker(QThread):
    """Roda a consulta opcional a DeepSeek (lenta) fora da thread de UI."""

    done = pyqtSignal()

    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller

    def run(self) -> None:
        self.controller.query_ai_async()
        self.done.emit()


# Cor (hex) por categoria de tarefa, alinhada a legenda do guia.
from .schedule import KIND_COLOR, KIND_LABEL


def _task_line_html(task: dict, for_overlay: bool = False) -> str:
    """Formata uma tarefa como linha HTML colorida pela categoria."""
    kind = task.get("kind", "normal")
    color = KIND_COLOR.get(kind, "#222222")
    if for_overlay:
        color = "#ffffff"
    label = KIND_LABEL.get(kind, "")
    tag = f"<b>[{label}]</b> " if label else ""
    text = escape(task.get("text") or "")
    return (
        f'<div style="color:{color}; margin:4px 0; line-height:1.35; '
        f'font-size:10.5pt;">&#8226; {tag}{text}</div>'
    )


class OverlayWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(400, 450)
        self.move(screen.width() - 420, 40)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self.frame = QFrame()
        self.frame.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 200);
                border-radius: 8px;
            }
            QLabel {
                color: white;
                background: transparent;
                font-family: "Segoe UI", sans-serif;
                font-size: 11pt;
            }
        """)
        
        frame_lay = QVBoxLayout(self.frame)
        frame_lay.setContentsMargins(15, 15, 15, 15)
        
        self.header = QLabel("📅 — | 💾 —")
        self.header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #f1c40f;")
        
        self.now_label = QLabel("✅ AGORA:")
        self.now_label.setStyleSheet("font-weight: bold; color: #2ecc71;")
        self.now_text = QLabel("—")
        self.now_text.setWordWrap(True)
        self.now_text.setTextFormat(Qt.TextFormat.RichText)
        
        self.warn_label = QLabel("⚠️ Avisos:")
        self.warn_label.setStyleSheet("font-weight: bold; color: #e74c3c;")
        self.warn_text = QLabel("—")
        self.warn_text.setWordWrap(True)
        self.warn_text.setTextFormat(Qt.TextFormat.RichText)
        
        frame_lay.addWidget(self.header)
        frame_lay.addWidget(self.now_label)
        frame_lay.addWidget(self.now_text)
        frame_lay.addWidget(self.warn_label)
        frame_lay.addWidget(self.warn_text)
        frame_lay.addStretch(1)
        
        self.scroll.setWidget(self.frame)
        lay.addWidget(self.scroll)

    def render(self, header_text, now_title, now_html, warn_title, warn_html):
        self.header.setText(header_text)
        self.now_label.setText(now_title)
        self.now_text.setText(now_html)
        self.warn_label.setText(warn_title)
        self.warn_text.setText(warn_html)


class SettingsDialog(QDialog):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Configuracoes")
        self.setMinimumWidth(420)

        cfg = controller.config
        layout = QFormLayout(self)

        self.api_key = QLineEdit(cfg.get("deepseek_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QLineEdit(cfg.get("deepseek_model", "deepseek-chat"))
        self.save_path = QLineEdit(cfg.get("save_path", "auto"))
        self.save_slot = QLineEdit(cfg.get("save_slot", "DATA01"))
        self.poll = QLineEdit(str(cfg.get("poll_interval_seconds", 10)))

        layout.addRow("DeepSeek API key:", self.api_key)
        layout.addRow("Modelo:", self.model)
        layout.addRow("Caminho do save:", self.save_path)
        layout.addRow("Slot:", self.save_slot)
        layout.addRow("Poll (s):", self.poll)

        btns = QHBoxLayout()
        ok = QPushButton("Salvar")
        cancel = QPushButton("Cancelar")
        ok.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addRow(btns)

    def _save(self) -> None:
        cfg = self.controller.config
        cfg["deepseek_api_key"] = self.api_key.text().strip()
        cfg["deepseek_model"] = self.model.text().strip() or "deepseek-chat"
        cfg["save_path"] = self.save_path.text().strip() or "auto"
        cfg["save_slot"] = self.save_slot.text().strip() or "DATA01"
        try:
            cfg["poll_interval_seconds"] = int(self.poll.text().strip())
        except ValueError:
            cfg["poll_interval_seconds"] = 10
        save_config(cfg)
        QMessageBox.information(self, "Config", "Salvo. Reinicie para aplicar caminhos.")
        self.accept()


class ManualProgressDialog(QDialog):
    """Entrada manual de social stats e ranks de confidant.

    Funciona como fallback ate os offsets de memoria serem mapeados. Campos cuja
    origem ja e leitura ao vivo ('memory') aparecem desabilitados, pois a memoria
    sempre assume sobre o valor digitado.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.sm = controller.state_mgr
        self.setWindowTitle("Meu Progresso (entrada manual)")
        self.setMinimumSize(420, 520)

        from .state_manager import CONFIDANT_NAMES, LIVE_SOURCES, SOCIAL_STAT_KEYS
        self.CONFIDANT_NAMES = CONFIDANT_NAMES
        self.SOCIAL_STAT_KEYS = SOCIAL_STAT_KEYS
        self.LIVE_SOURCES = LIVE_SOURCES

        root = QVBoxLayout(self)

        info = QLabel(
            "Digite seu progresso atual. Isto e usado ate o app conseguir ler da\n"
            "memoria do jogo — quando isso acontecer, a leitura ao vivo assume\n"
            "automaticamente (campos travados = ja lidos do jogo)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#555; font-size:10pt;")
        root.addWidget(info)

        # --- Data (caso jogo offline) ---
        date_box = QHBoxLayout()
        d = self.sm.state.get("in_game_date") or {}
        self.in_month = QSpinBox(); self.in_month.setRange(1, 12)
        self.in_day = QSpinBox(); self.in_day.setRange(1, 31)
        self.in_month.setValue(d.get("month") or 4)
        self.in_day.setValue(d.get("day") or 11)
        date_locked = self.sm.source_of("in_game_date") in self.LIVE_SOURCES
        self.in_month.setEnabled(not date_locked)
        self.in_day.setEnabled(not date_locked)
        date_box.addWidget(QLabel("Data:"))
        date_box.addWidget(QLabel("Mes")); date_box.addWidget(self.in_month)
        date_box.addWidget(QLabel("Dia")); date_box.addWidget(self.in_day)
        if date_locked:
            date_box.addWidget(QLabel("(lido do jogo)"))
        date_box.addStretch(1)
        root.addLayout(date_box)

        # --- Social stats ---
        root.addWidget(QLabel("Social Stats (rank 1-5):"))
        stats_grid = QGridLayout()
        self.stat_spins: dict = {}
        cur_stats = self.sm.state.get("social_stats") or {}
        labels = {"knowledge": "Knowledge", "charm": "Charm", "guts": "Guts",
                  "kindness": "Kindness", "proficiency": "Proficiency"}
        for i, k in enumerate(self.SOCIAL_STAT_KEYS):
            sp = QSpinBox(); sp.setRange(0, 5)
            sp.setValue(cur_stats.get(k) or 0)
            locked = self.sm.source_of(f"social_stats.{k}") in self.LIVE_SOURCES
            sp.setEnabled(not locked)
            lbl = labels[k] + (" 🔒" if locked else "")
            stats_grid.addWidget(QLabel(lbl), i // 2, (i % 2) * 2)
            stats_grid.addWidget(sp, i // 2, (i % 2) * 2 + 1)
            self.stat_spins[k] = sp
        root.addLayout(stats_grid)

        # --- Confidants (scroll) ---
        root.addWidget(QLabel("Confidants (rank 0-10; 0 = nao iniciado):"))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); grid = QGridLayout(inner)
        self.conf_spins: dict = {}
        cur_conf = self.sm.state.get("confidants") or {}
        for i, name in enumerate(self.CONFIDANT_NAMES):
            sp = QSpinBox(); sp.setRange(0, 10)
            sp.setValue(int(cur_conf.get(name) or 0))
            locked = self.sm.source_of(f"confidants.{name}") in self.LIVE_SOURCES
            sp.setEnabled(not locked)
            lbl = name + (" 🔒" if locked else "")
            grid.addWidget(QLabel(lbl), i // 2, (i % 2) * 2)
            grid.addWidget(sp, i // 2, (i % 2) * 2 + 1)
            self.conf_spins[name] = sp
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # --- Botoes ---
        btns = QHBoxLayout()
        ok = QPushButton("Salvar progresso")
        cancel = QPushButton("Cancelar")
        ok.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        root.addLayout(btns)

    def _save(self) -> None:
        # Data (so se editavel)
        if self.in_month.isEnabled():
            self.sm.set_manual_date(self.in_month.value(), self.in_day.value())
        # Stats: 0 significa "nao informado" -> envia None para nao mascarar
        stats = {k: (sp.value() or None) for k, sp in self.stat_spins.items()
                 if sp.isEnabled()}
        self.sm.set_manual_stats(stats)
        # Confidants: grava todos os editaveis (0 = nao iniciado, valido)
        conf = {name: sp.value() for name, sp in self.conf_spins.items()
                if sp.isEnabled()}
        self.sm.set_manual_confidants(conf)
        self.sm.save()
        self.accept()


class MainWindow(QWidget):
    update_signal = pyqtSignal()
    querying_signal = pyqtSignal()

    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller
        self._worker: Optional[RefreshWorker] = None
        self._ai_worker: Optional[AiWorker] = None

        self.overlay = OverlayWindow()

        self.setWindowTitle("Platinum Assistant - P5R")
        self.setMinimumSize(500, 520)
        self.resize(520, 560)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self._build_ui()
        self._build_tray()

        # controller.refresh() (thread do worker) avisa via callback -> sinal Qt.
        self.update_signal.connect(self._render)
        self.querying_signal.connect(self._show_querying)
        self.controller._on_update = lambda _c: self.update_signal.emit()
        self.controller._on_querying = lambda _c: self.querying_signal.emit()

    # ---- construcao da UI ---------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        self.title = QLabel("🎭 Platinum Assistant — P5R")
        self.title.setStyleSheet("font-weight: 700; font-size: 14pt;")
        root.addWidget(self.title)

        self.header = QLabel("📅 — | 💾 —")
        self.header.setStyleSheet("color: #333; font-size: 11.5pt;")
        root.addWidget(self.header)

        # Linha de status da IA (opcional — o guia e a fonte primaria).
        self.api_status = QLabel("🤖 IA opcional — clique 'IA' para perguntar")
        self.api_status.setStyleSheet("color: #666; font-size: 10pt;")
        root.addWidget(self.api_status)

        root.addWidget(self._hline())

        self.now_label = QLabel("✅ AGORA (passos do dia):")
        self.now_label.setStyleSheet("font-weight: 700; font-size: 11.5pt;")
        root.addWidget(self.now_label)
        self.now_text = QLabel("Defina a data (jogo aberto ou Meu Progresso) para ver os passos.")
        self.now_text.setWordWrap(True)
        self.now_text.setTextFormat(Qt.TextFormat.RichText)
        self.now_text.setStyleSheet("font-size: 10.5pt; color: #202124;")
        root.addWidget(self.now_text)

        self.warn_label = QLabel("⚠️ Não pode perder hoje:")
        self.warn_label.setStyleSheet("font-weight: 700; color: #9a5b00; font-size: 11.5pt;")
        root.addWidget(self.warn_label)
        self.warn_text = QLabel("—")
        self.warn_text.setWordWrap(True)
        self.warn_text.setTextFormat(Qt.TextFormat.RichText)
        self.warn_text.setStyleSheet("font-size: 10.5pt; color: #202124;")
        root.addWidget(self.warn_text)

        self.stats_text = QLabel("📊 Stats: —")
        self.stats_text.setStyleSheet("color: #333; font-size: 10.5pt;")
        root.addWidget(self.stats_text)

        root.addStretch(1)
        root.addWidget(self._hline())

        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Atualizar")
        self.btn_plan = QPushButton("Plano")
        self.btn_progress = QPushButton("Meu Progresso")
        self.btn_ai = QPushButton("IA")
        self.btn_ai.setToolTip("Perguntar à DeepSeek (opcional, ~13s)")
        self.btn_ai.setFixedWidth(48)
        self.btn_overlay = QPushButton("Overlay")
        self.btn_cfg = QPushButton("⚙")
        self.btn_cfg.setFixedWidth(42)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_plan.clicked.connect(self._show_plan)
        self.btn_progress.clicked.connect(self._show_progress)
        self.btn_ai.clicked.connect(self.ask_ai)
        self.btn_overlay.clicked.connect(self.toggle_overlay)
        self.btn_cfg.clicked.connect(self._show_settings)
        btns.addWidget(self.btn_refresh)
        btns.addWidget(self.btn_plan)
        btns.addWidget(self.btn_progress)
        btns.addWidget(self.btn_ai)
        btns.addWidget(self.btn_overlay)
        btns.addWidget(self.btn_cfg)
        root.addLayout(btns)

    def _hline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_color_icon("yellow"), self)
        self.tray.setToolTip("Platinum Assistant")
        menu = QMenu()
        act_show = QAction("Mostrar", self)
        act_refresh = QAction("Atualizar", self)
        act_quit = QAction("Sair", self)
        act_show.triggered.connect(self._restore)
        act_refresh.triggered.connect(self.refresh)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_show)
        menu.addAction(act_refresh)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    # ---- acoes ---------------------------------------------------------
    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
            self.btn_overlay.setStyleSheet("")
        else:
            self.overlay.show()
            self.btn_overlay.setStyleSheet("background-color: #2ecc71; color: white;")
            self._render()

    def refresh(self) -> None:
        # Atualizacao rapida: le memoria + lookup local do guia (sem API).
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Atualizando...")
        self._worker = RefreshWorker(self.controller)
        self._worker.done.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_done(self) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Atualizar")
        self._render()

    def ask_ai(self) -> None:
        # Consulta opcional e lenta a DeepSeek, sob demanda.
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("…")
        self._show_querying()
        self._ai_worker = AiWorker(self.controller)
        self._ai_worker.done.connect(self._on_ai_done)
        self._ai_worker.start()

    def _on_ai_done(self) -> None:
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("IA")
        g = self.controller.status.guidance or {}
        self._render()
        # Mostra a resposta da IA num dialogo dedicado (nao polui o bloco do guia).
        if g and "_error" not in g:
            self._show_ai_result(g)

    def _show_querying(self) -> None:
        """Feedback imediato enquanto a consulta (lenta) a DeepSeek roda."""
        self.api_status.setText("🤖 DeepSeek: consultando… (~13s)")
        self.api_status.setStyleSheet("color: #1f6f9f; font-size: 10pt;")

    def _render(self) -> None:
        st = self.controller.status
        state = self.controller.state_mgr

        self.header.setText(
            f"📅 {state.date_str}  |  💾 {state.state.get('last_updated') or '—'}"
        )

        # Linha de status da IA (opcional).
        g_now = st.guidance or {}
        if st.querying:
            self.api_status.setText("🤖 DeepSeek: consultando… (~13s)")
            self.api_status.setStyleSheet("color: #1f6f9f; font-size: 10pt;")
        elif not st.api_configured:
            self.api_status.setText("🤖 IA: sem chave — configure em ⚙ (opcional)")
            self.api_status.setStyleSheet("color: #666; font-size: 10pt;")
        elif not g_now:
            self.api_status.setText("🤖 IA opcional — clique 'IA' para perguntar")
            self.api_status.setStyleSheet("color: #666; font-size: 10pt;")
        elif st.online:
            self.api_status.setText("🤖 DeepSeek: respondeu ✓")
            self.api_status.setStyleSheet("color: #218c53; font-size: 10pt;")
        else:
            self.api_status.setText("🤖 DeepSeek: erro na última consulta")
            self.api_status.setStyleSheet("color: #a93226; font-size: 10pt;")

        # --- AGORA: passos do dia vindos do guia (fonte primaria) ---
        date = state.state.get("in_game_date") or {}
        period = (date.get("period") or "").lower()
        is_night = any(w in period for w in ("night", "evening", "noite"))
        self.now_label.setText(
            "🌙 AGORA (noite):" if is_night else "✅ AGORA (dia):"
        )
        entry = st.schedule_today
        if entry is None:
            if date.get("month") is None:
                self.now_text.setText(
                    "Defina a data (abra o jogo ou use <b>Meu Progresso</b>) "
                    "para ver os passos do dia."
                )
            else:
                self.now_text.setText(
                    f"O guia não tem entrada para {state.date_str} "
                    "(provável dia de cutscene/sem ações)."
                )
        elif not st.schedule_period:
            self.now_text.setText("Nada agendado neste período. Avance o tempo.")
        else:
            self.now_text.setText(
                "".join(_task_line_html(t) for t in st.schedule_period)
            )

        # --- Nao pode perder hoje: itens/infiltracoes do cronograma + missables ---
        crit_html = [_task_line_html(t) for t in st.schedule_critical]
        for m in st.upcoming[:3]:
            u = m.get("urgency_days")
            when = f"em {u}d" if u is not None else ""
            crit_html.append(
                f'<div style="color:#b9770e; margin:2px 0;">• {m.get("month")}/'
                f'{m.get("day")} {when}: {m.get("message")}</div>'
            )
        self.warn_text.setText(
            "".join(crit_html) if crit_html else "Nada crítico hoje."
        )

        stats = state.state.get("social_stats") or {}
        def s(k):
            v = stats.get(k)
            return "?" if v is None else v
        yen = state.state.get("yen")
        yen_txt = f"💴 {yen:,}".replace(",", ".") if isinstance(yen, int) else "💴 ?"
        self.stats_text.setText(
            f"📊 Kno:{s('knowledge')} Chr:{s('charm')} Gut:{s('guts')} "
            f"Kin:{s('kindness')} Pro:{s('proficiency')}   {yen_txt}"
            + ("" if st.game_running else "   (jogo offline)")
        )

        color = self.controller.tray_color()
        self.tray.setIcon(_color_icon(color))

        if self.overlay.isVisible():
            now_html_overlay = ""
            if entry is None or not st.schedule_period:
                now_html_overlay = '<div style="color:white; margin:4px 0;">Sem ações para este período.</div>'
            else:
                now_html_overlay = "".join(_task_line_html(t, for_overlay=True) for t in st.schedule_period)
                
            warn_html_overlay = ""
            crit_html_overlay = [_task_line_html(t, for_overlay=True) for t in st.schedule_critical]
            for m in st.upcoming[:3]:
                u = m.get("urgency_days")
                when = f"em {u}d" if u is not None else ""
                crit_html_overlay.append(
                    f'<div style="color:#f1c40f; margin:2px 0;">• {m.get("month")}/'
                    f'{m.get("day")} {when}: {m.get("message")}</div>'
                )
            warn_html_overlay = "".join(crit_html_overlay) if crit_html_overlay else '<div style="color:white;">Nada crítico.</div>'
            
            self.overlay.render(
                self.header.text(),
                self.now_label.text(),
                now_html_overlay,
                self.warn_label.text(),
                warn_html_overlay
            )

    def _show_plan(self) -> None:
        """Plano completo do dia (Day + Night) direto do guia, com cores."""
        st = self.controller.status
        state = self.controller.state_mgr
        entry = st.schedule_today
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Plano do Dia — {state.date_str}")
        dlg.setMinimumSize(440, 420)
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)

        if entry is None:
            txt.setHtml("<p>Sem entrada no guia para esta data.</p>")
        else:
            parts: list[str] = []
            wd = entry.get("weekday")
            head = f"{state.date_str}"
            if wd:
                head += f" ({wd})"
            parts.append(f"<h3 style='margin:4px 0;'>{head}</h3>")
            if st.month_intro:
                parts.append(
                    f"<div style='color:#666; font-size:10pt; margin-bottom:6px;'>"
                    f"{st.month_intro}</div>"
                )
            day_tasks = entry.get("day") or []
            night_tasks = entry.get("night") or []
            parts.append("<b>☀️ Dia</b>")
            parts += [_task_line_html(t) for t in day_tasks] or [
                "<div style='color:#999;'>(nada)</div>"
            ]
            parts.append("<br><b>🌙 Noite</b>")
            parts += [_task_line_html(t) for t in night_tasks] or [
                "<div style='color:#999;'>(nada)</div>"
            ]
            txt.setHtml("".join(parts))
        lay.addWidget(txt)
        dlg.exec()

    def _show_ai_result(self, g: dict) -> None:
        """Mostra a resposta opcional da DeepSeek num dialogo separado."""
        plan = g.get("period_plan") or {}
        alerts = g.get("stat_alerts") or []
        warns = g.get("upcoming_warnings") or []
        dlg = QDialog(self)
        dlg.setWindowTitle("Sugestão da IA (DeepSeek)")
        dlg.setMinimumSize(420, 360)
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)
        prio = g.get("next_action_priority", "")
        body = [
            f"AGORA: {g.get('next_action', '—')}" + (f"  [{prio}]" if prio else ""),
            "",
            f"Manhã:  {plan.get('morning', '—')}",
            f"Tarde:  {plan.get('afternoon', '—')}",
            f"Noite:  {plan.get('evening', '—')}",
        ]
        if warns:
            body += ["", "Avisos:"]
            body += [f"  - ({w.get('urgency_days')}d) {w.get('message')}" for w in warns]
        if alerts:
            body += ["", "Alertas de stats:"] + [f"  - {a}" for a in alerts]
        txt.setPlainText("\n".join(body))
        lay.addWidget(txt)
        dlg.exec()

    def _show_settings(self) -> None:
        if SettingsDialog(self.controller, self).exec():
            # Chave/modelo podem ter mudado: re-consulta automaticamente.
            self.refresh()

    def _show_progress(self) -> None:
        dlg = ManualProgressDialog(self.controller, self)
        if dlg.exec():
            # Re-renderiza e re-consulta com o progresso atualizado.
            self.refresh()

    # ---- tray / janela -------------------------------------------------
    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore()

    def _restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        # Fechar minimiza para a tray em vez de encerrar.
        event.ignore()
        self.hide()
        if self.overlay.isVisible():
            self.overlay.hide()
            self.btn_overlay.setStyleSheet("")
        self.tray.showMessage(
            "Platinum Assistant",
            "Continua rodando na bandeja. Clique no icone para reabrir.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )


class HotkeySignals(QObject):
    toggle = pyqtSignal()

def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # vive na tray
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_STYLE)

    controller = Controller()
    window = MainWindow(controller)
    # A janela principal fica oculta por padrao, o app vive na system tray
    # window.show()
    
    # Abre o overlay por padrao na inicializacao
    window.toggle_overlay()

    # Registra o atalho global F9 via API nativa do Windows
    hk_signals = HotkeySignals()
    hk_signals.toggle.connect(window.toggle_overlay)
    threading.Thread(target=_native_hotkey_thread, args=(hk_signals,), daemon=True).start()

    # Inicia watcher + leitura inicial em background.
    threading.Thread(target=lambda: controller.start(
        on_update=lambda _c: window.update_signal.emit()
    ), daemon=True).start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui())
