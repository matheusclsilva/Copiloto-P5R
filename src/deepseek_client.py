"""Integracao com a DeepSeek API (item 5).

DeepSeek e compativel com o formato OpenAI, entao usamos o SDK ``openai`` com
``base_url`` apontando para a DeepSeek. A funcao principal recebe o estado atual
+ contexto do guia e retorna orientacao estruturada em JSON.

A API key vem da config (nunca hardcoded). Falhas de rede/parse sao tratadas
para que o app continue funcionando em modo offline com os missables hardcoded.
"""

from __future__ import annotations

import json
from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore

    _OPENAI_AVAILABLE = True
except Exception:
    OpenAI = None  # type: ignore
    _OPENAI_AVAILABLE = False


SYSTEM_PROMPT = """
Voce e um assistant especialista em Persona 5 Royal focado em ajudar o jogador
a conseguir 100% e a platina em uma unica run, seguindo o Perfect Schedule do PSNProfiles.

IDIOMA OBRIGATORIO: responda SEMPRE em portugues do Brasil (pt-BR). TODAS as
strings de texto do JSON (next_action, message, period_plan, stat_alerts) devem
estar em portugues do Brasil, com naturalidade. Mantenha nomes proprios do jogo
(Confidants, Personas, Palaces, locais) no original em ingles quando for o nome
canonico usado no jogo, mas todo o resto da frase em pt-BR.

Voce recebera o estado atual do save do jogador e deve responder APENAS com JSON valido,
sem markdown, sem blocos de codigo, sem texto fora do JSON. Formato exato:
{
  "next_action": "descricao clara do que fazer AGORA (periodo atual)",
  "next_action_priority": "obrigatorio|recomendado|opcional",
  "upcoming_warnings": [
    {
      "urgency_days": 3,
      "message": "descricao do missable ou acao critica"
    }
  ],
  "period_plan": {
    "morning": "acao para manha se aplicavel",
    "afternoon": "acao para tarde",
    "evening": "acao para noite"
  },
  "stat_alerts": ["alerta se algum stat esta atrasado para os requisitos"]
}

Priorize: 1) Missables que fecham em breve, 2) Confidants com janelas apertadas, 3) Stats atrasados.
""".strip()


class DeepSeekError(Exception):
    pass


def get_client(config: dict) -> "OpenAI":
    """Constroi o cliente OpenAI apontado para a DeepSeek."""
    if not _OPENAI_AVAILABLE:
        raise DeepSeekError(
            "SDK 'openai' nao instalado. Rode: pip install -r requirements.txt"
        )
    api_key = config.get("deepseek_api_key")
    if not api_key:
        raise DeepSeekError(
            "deepseek_api_key vazia no config.json. Configure sua chave da DeepSeek."
        )
    return OpenAI(
        api_key=api_key,
        base_url=config.get("deepseek_base_url", "https://api.deepseek.com"),
    )


def build_user_message(state: dict, guide_context: str) -> str:
    return (
        "Estado atual do save:\n"
        f"{json.dumps(state, indent=2, ensure_ascii=False)}\n\n"
        "Contexto do dia no guia Perfect Schedule:\n"
        f"{guide_context if guide_context else 'Nao disponivel - use seu conhecimento do jogo.'}\n\n"
        "O que devo fazer agora?"
    )


def query_guidance(client: "OpenAI", state: dict, guide_context: str = "",
                   model: str = "deepseek-chat") -> dict[str, Any]:
    """Consulta a DeepSeek e retorna a orientacao ja parseada (dict)."""
    user_message = build_user_message(state, guide_context)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise DeepSeekError(f"Falha na chamada a DeepSeek: {exc}") from exc

    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"Resposta nao e JSON valido: {exc}\nConteudo: {raw[:300]}") from exc


def safe_query_guidance(config: dict, state: dict, guide_context: str = "") -> dict[str, Any]:
    """Versao tolerante a falhas: nunca levanta; retorna dict com '_error' se falhar."""
    try:
        client = get_client(config)
        return query_guidance(
            client, state, guide_context, model=config.get("deepseek_model", "deepseek-chat")
        )
    except Exception as exc:
        return {"_error": str(exc)}
