"""Traduz guides/p5r_schedule.json para pt-BR -> guides/p5r_schedule_pt.json.

Estrategia (offline depois de rodar uma vez):
  - Coleta os textos UNICOS das tarefas (Day/Night) + month_intro + color_legend.
  - Traduz em LOTES via DeepSeek (JSON in/out), preservando nomes proprios do
    jogo (Confidants, Personas, Palaces, locais) no original em ingles.
  - Cacheia um dicionario en->pt em guides/_translation_cache.json para que
    reexecucoes (guia atualizado) so traduzam o que for novo.
  - Reconstroi o schedule mantendo estrutura/kind/weekday e grava _pt.json.

Uso:
    python translate_schedule.py            # traduz o que falta e grava _pt.json
    python translate_schedule.py --redo     # ignora o cache e retraduz tudo

Nada aqui toca no save do jogo nem no JSON original em ingles.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from src.config_manager import PROJECT_ROOT, load_config
from src.deepseek_client import get_client

SCHEDULE_EN = PROJECT_ROOT / "guides" / "p5r_schedule.json"
SCHEDULE_PT = PROJECT_ROOT / "guides" / "p5r_schedule_pt.json"
CACHE_PATH = PROJECT_ROOT / "guides" / "_translation_cache.json"

BATCH_SIZE = 40  # textos por chamada a API

TRANSLATE_SYSTEM = (
    "Voce e um tradutor especialista em jogos, traduzindo um guia de Persona 5 "
    "Royal do ingles para o portugues do Brasil (pt-BR). Regras:\n"
    "1. Traduza com naturalidade, tom de guia de jogo (imperativo: 'Fale com...', "
    "'Va ate...', 'Examine...').\n"
    "2. MANTENHA no original em ingles os nomes proprios do jogo: nomes de "
    "Confidants/personagens (Ryuji, Makoto, Kawakami...), Personas, Palaces, "
    "locais (LeBlanc, Backstreets, Shibuya, Mementos), itens com nome proprio e "
    "termos canonicos (Confidant, Baton Pass, Technical, Showtime, Will Seed).\n"
    "3. Preserve numeros, datas e ranks exatamente.\n"
    "4. Voce recebe um objeto JSON {\"<id>\": \"texto em ingles\", ...} e responde "
    "APENAS com um JSON {\"<id>\": \"traducao pt-BR\", ...} com as MESMAS chaves, "
    "sem markdown, sem texto fora do JSON."
)


def load_json(p: Path, default: Any) -> Any:
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return default


def collect_unique_texts(sched: dict) -> list[str]:
    seen: dict[str, None] = {}  # preserva ordem de aparicao
    for v in sched.get("days", {}).values():
        for slot in ("day", "night"):
            for t in v.get(slot) or []:
                txt = t.get("text", "")
                if txt:
                    seen.setdefault(txt, None)
    for intro in sched.get("month_intro", {}).values():
        if intro:
            seen.setdefault(intro, None)
    for legend in sched.get("color_legend", {}).values():
        if legend:
            seen.setdefault(legend, None)
    return list(seen.keys())


def translate_batch(client, model: str, batch: list[str]) -> dict[str, str]:
    """Traduz um lote; usa indices como chave para evitar ambiguidade."""
    payload = {str(i): txt for i, txt in enumerate(batch)}
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    out = json.loads(raw)
    result: dict[str, str] = {}
    for i, txt in enumerate(batch):
        tr = out.get(str(i))
        result[txt] = tr if isinstance(tr, str) and tr.strip() else txt
    return result


def rebuild(sched: dict, cache: dict[str, str]) -> dict:
    def tr(s: str) -> str:
        return cache.get(s, s)

    new_days: dict[str, Any] = {}
    for key, v in sched.get("days", {}).items():
        entry = dict(v)
        for slot in ("day", "night"):
            entry[slot] = [
                {**t, "text": tr(t.get("text", ""))} for t in (v.get(slot) or [])
            ]
        new_days[key] = entry

    return {
        **{k: sched[k] for k in ("source", "title") if k in sched},
        "language": "pt-BR",
        "color_legend": {k: tr(val) for k, val in sched.get("color_legend", {}).items()},
        "month_intro": {k: tr(val) for k, val in sched.get("month_intro", {}).items()},
        "days": new_days,
    }


def main(argv: list[str]) -> int:
    redo = "--redo" in argv
    sched = load_json(SCHEDULE_EN, None)
    if sched is None:
        print(f"ERRO: {SCHEDULE_EN} nao existe. Rode build_schedule.py primeiro.")
        return 1

    cache: dict[str, str] = {} if redo else load_json(CACHE_PATH, {})
    texts = collect_unique_texts(sched)
    todo = [t for t in texts if t not in cache]
    print(f"Textos unicos: {len(texts)} | ja em cache: {len(texts)-len(todo)} | a traduzir: {len(todo)}")

    if todo:
        config = load_config()
        model = config.get("deepseek_model", "deepseek-chat")
        client = get_client(config)
        total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
        for bi in range(total_batches):
            batch = todo[bi * BATCH_SIZE:(bi + 1) * BATCH_SIZE]
            for attempt in range(3):
                try:
                    cache.update(translate_batch(client, model, batch))
                    break
                except Exception as exc:
                    wait = 2 * (attempt + 1)
                    print(f"  lote {bi+1}/{total_batches} falhou (tentativa {attempt+1}): {exc} — retry em {wait}s")
                    time.sleep(wait)
            else:
                print(f"  lote {bi+1}/{total_batches} desistiu; mantem ingles nesses textos.")
            print(f"  lote {bi+1}/{total_batches} OK ({len(cache)} no cache)")
            # Persiste o cache a cada lote para nao perder progresso.
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out = rebuild(sched, cache)
    SCHEDULE_PT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {SCHEDULE_PT} ({len(out['days'])} dias)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
