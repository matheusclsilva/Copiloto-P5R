"""Constroi guides/p5r_schedule.json a partir do MHTML do guia PSNProfiles.

Uso (uma vez, ou quando o guia for atualizado):
    python build_schedule.py

Le o arquivo .mhtml salvo em Guia/, extrai o HTML (UTF-8), e transforma o
cronograma dia-a-dia (tabelas Date | Day | Night de cada mes) num JSON indexado
por "M/D". Cada tarefa (<li>) e classificada pela cor usada no guia:

  verde   -> rank up de Confidant / Baton Pass / Technical
  azul    -> ganho de stats / social stats
  vermelho-> infiltracao de Palace/dungeon
  cinza   -> side quests
  laranja -> itens / bonus recebidos (NAO pode perder = void 100%)

O dataset gerado e a fonte primaria do app: data de hoje -> passos do dia.
"""

from __future__ import annotations

import email
import json
import re
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

PROJECT_ROOT = Path(__file__).resolve().parent
GUIDE_DIR = PROJECT_ROOT / "Guia"
OUTPUT_PATH = PROJECT_ROOT / "guides" / "p5r_schedule.json"
GUIDE_URL = "https://psnprofiles.com/guide/11946-persona-5-royal-100-perfect-schedule"

# Ordem dos meses no calendario do jogo (abril -> fevereiro do ano seguinte).
MONTH_HEADINGS = [
    ("April", [4]),
    ("May", [5]),
    ("June", [6]),
    ("July", [7]),
    ("August", [8]),
    ("September", [9]),
    ("October", [10]),
    ("November", [11]),
    ("December", [12]),
    ("January/February", [1, 2]),
]
# Heading que marca o fim do cronograma (vem logo apos January/February).
END_HEADING = "Recommended Fusion Path"

# Mapa cor (rgb) -> categoria semantica, conforme a legenda do guia.
COLOR_KIND = {
    "rgb(65,168,95)": "confidant",   # verde
    "rgb(44,130,201)": "stat",       # azul
    "rgb(209,72,65)": "dungeon",     # vermelho
    "rgb(119,119,119)": "sidequest", # cinza
    "rgb(102,102,102)": "sidequest", # cinza (variacao)
    "rgb(243,121,52)": "item",       # laranja
}

COLOR_LEGEND = {
    "confidant": "Rank up de Confidant / Baton Pass / Technical (verde)",
    "stat": "Ganho de stats / social stats (azul)",
    "dungeon": "Infiltracao de Palace/dungeon (vermelho)",
    "sidequest": "Side quest recebida / confirmada (cinza)",
    "item": "Item ou bonus recebido — NAO pode perder (laranja)",
    "normal": "Acao comum do dia",
}


def extract_html() -> str:
    """Extrai o text/html (UTF-8) do .mhtml em Guia/."""
    mhtml = next(GUIDE_DIR.glob("*.mhtml"), None)
    if mhtml is None:
        raise SystemExit(f"Nenhum .mhtml encontrado em {GUIDE_DIR}")
    msg = email.message_from_bytes(mhtml.read_bytes())
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8", errors="replace")
    raise SystemExit("Parte text/html nao encontrada no MHTML")


def _kind_of(li: Tag) -> str:
    """Classifica um <li> pela cor do primeiro span colorido que contiver."""
    for span in li.find_all("span"):
        style = (span.get("style") or "").replace(" ", "")
        for rgb, kind in COLOR_KIND.items():
            if rgb.replace(" ", "") in style:
                return kind
    return "normal"


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(";").strip()


def _tasks_from_cell(cell: Optional[Tag]) -> list[dict[str, str]]:
    """Extrai a lista de tarefas (<li>) de uma celula Day ou Night."""
    if cell is None:
        return []
    tasks: list[dict[str, str]] = []
    items = cell.find_all("li")
    if items:
        for li in items:
            text = _clean(li.get_text(" ", strip=True))
            if text:
                tasks.append({"text": text, "kind": _kind_of(li)})
    else:
        # Algumas celulas trazem texto solto sem <li>.
        text = _clean(cell.get_text(" ", strip=True))
        if text:
            tasks.append({"text": text, "kind": "normal"})
    return tasks


def _heading_pos(soup: BeautifulSoup, name: str) -> Optional[int]:
    h = soup.find(lambda t: t.name == "h3" and t.get_text(strip=True) == name)
    return h.sourceline if h else None


def build() -> dict[str, Any]:
    soup = BeautifulSoup(extract_html(), "html.parser")

    # Posicoes (sourceline) de cada heading de mes + o heading final.
    bounds: list[tuple[str, list[int], int]] = []
    for name, months in MONTH_HEADINGS:
        pos = _heading_pos(soup, name)
        if pos is None:
            raise SystemExit(f"Heading do mes nao encontrado: {name}")
        bounds.append((name, months, pos))
    end_pos = _heading_pos(soup, END_HEADING) or 10**9

    all_tables = soup.find_all("table")
    days: dict[str, dict[str, Any]] = {}
    month_intro: dict[str, str] = {}

    for idx, (name, months, start) in enumerate(bounds):
        stop = bounds[idx + 1][2] if idx + 1 < len(bounds) else end_pos
        tables = [t for t in all_tables if start < (t.sourceline or 0) < stop]

        intro_saved = False
        for tbl in tables:
            for row in tbl.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue
                first = cells[0].get_text(" ", strip=True)
                m = re.match(r"(\d\d)/(\d\d)", first)
                if not m:
                    # Linha de intro/observacao do mes (texto corrido). Guarda a
                    # primeira como contexto do mes.
                    if not intro_saved:
                        txt = _clean(row.get_text(" ", strip=True))
                        # Ignora cabecalho "Date Day Night".
                        if txt and not re.match(r"^Date\s+Day", txt):
                            month_intro.setdefault(str(months[0]), txt)
                            intro_saved = True
                    continue

                month = int(m.group(1))
                day = int(m.group(2))
                weekday = ""
                wd = re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)", first)
                if wd:
                    weekday = wd.group(1)

                day_tasks = _tasks_from_cell(cells[1] if len(cells) > 1 else None)
                night_tasks = _tasks_from_cell(cells[2] if len(cells) > 2 else None)

                key = f"{month}/{day}"
                days[key] = {
                    "weekday": weekday,
                    "month_name": name,
                    "day": day_tasks,
                    "night": night_tasks,
                }

    return {
        "source": GUIDE_URL,
        "title": "Persona 5 Royal - 100% Perfect Schedule",
        "color_legend": COLOR_LEGEND,
        "month_intro": month_intro,
        "days": days,
    }


def main() -> int:
    data = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    days = data["days"]
    print(f"OK: {len(days)} dias escritos em {OUTPUT_PATH}")
    # Resumo por mes para conferencia.
    from collections import Counter
    by_month = Counter(int(k.split('/')[0]) for k in days)
    for mth in [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2]:
        if by_month.get(mth):
            print(f"  mes {mth:2d}: {by_month[mth]} dias")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
