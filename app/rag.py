import json
import logging
import math
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .config import settings
from .db import DB

logger = logging.getLogger(__name__)

_embedding_client: Optional[AsyncOpenAI] = None
_chat_client: Optional[AsyncOpenAI] = None

RAG_WINDOWS = {
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}

RAG_ACTION_FILTERS = {
    "incidents": None,
    "mutes": "mute",
    "warnings": "warn",
}


def _safe_text(value: Optional[str], limit: int = 160) -> str:
    if not value:
        return ""
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…"


def _safe_metadata(metadata_json: str) -> Dict[str, Any]:
    try:
        raw = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError:
        return {}

    allowed_keys = {
        "violations",
        "warn_threshold",
        "mute_threshold",
        "language",
        "language_mode",
        "flood",
        "ai_summary",
        "field",
        "old",
        "new",
        "query",
        "window",
        "filter",
        "result_ids",
    }
    safe: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, str):
            safe[key] = _safe_text(value, limit=200)
        else:
            safe[key] = value
    if "ai_summary" in safe:
        safe["ai_summary"] = _safe_text(str(safe["ai_summary"]), limit=200)
    return safe


def _event_display_ts(ts: int) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


def _event_to_safe_record(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "audit_id": event["event_id"],
        "ts": _event_display_ts(int(event["ts"])),
        "action": event.get("action", "unknown"),
        "reason": _safe_text(event.get("reason")),
        "actor_user_id": event.get("actor_user_id"),
        "target_user_id": event.get("target_user_id"),
        "metadata": _safe_metadata(event.get("metadata_json", "")),
    }


def _build_event_text(event: Dict[str, Any]) -> str:
    safe = _event_to_safe_record(event)
    parts = [
        f"action={safe['action']}",
        f"reason={safe['reason']}",
        f"actor={safe['actor_user_id']}",
        f"target={safe['target_user_id']}",
    ]
    metadata = safe.get("metadata", {})
    for key, value in metadata.items():
        parts.append(f"{key}={value}")
    return " | ".join([p for p in parts if p and p != "None"])


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _score_keyword_match(query: str, event: Dict[str, Any]) -> float:
    tokens = [token for token in query.lower().split() if token]
    if not tokens:
        return 0.0
    haystack = " ".join(
        [
            str(event.get("action", "")).lower(),
            str(event.get("reason", "")).lower(),
            str(event.get("metadata_json", "")).lower(),
        ]
    )
    score = sum(1 for token in tokens if token in haystack)
    return float(score)


def _get_embedding_client() -> Optional[AsyncOpenAI]:
    global _embedding_client
    if _embedding_client is not None:
        return _embedding_client
    if not settings.openai_api_key:
        return None
    try:
        _embedding_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=12.0)
        return _embedding_client
    except Exception as exc:
        logger.warning("Embedding client init failed: %s", exc)
        return None


def _get_chat_client() -> Optional[AsyncOpenAI]:
    global _chat_client
    if _chat_client is not None:
        return _chat_client
    if not settings.openai_api_key or not settings.use_llm:
        return None
    try:
        _chat_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=12.0)
        return _chat_client
    except Exception as exc:
        logger.warning("Chat client init failed: %s", exc)
        return None


async def _embed_texts(texts: List[str]) -> List[List[float]]:
    client = _get_embedding_client()
    if not client or not texts:
        return []
    try:
        response = await client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
        return [record.embedding for record in response.data]
    except Exception as exc:
        logger.warning("Embedding generation failed: %s", exc)
        return []


async def retrieve_audit_events(
    db: DB,
    chat_id: int,
    query: str,
    window_key: str,
    action_filter_key: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    window_seconds = RAG_WINDOWS.get(window_key, RAG_WINDOWS["24h"])
    since_ts = int(time.time()) - window_seconds
    action_filter = RAG_ACTION_FILTERS.get(action_filter_key)
    base_events = db.search_audit_events(
        chat_id=chat_id,
        query_text=query,
        since_ts=since_ts,
        action_filter=action_filter,
        limit=200,
    )
    if not base_events:
        return []

    if not settings.openai_api_key:
        scored = [
            (event, _score_keyword_match(query, event))
            for event in base_events
        ]
        scored.sort(key=lambda item: (item[1], item[0].get("ts", 0)), reverse=True)
        return [event for event, _ in scored[:top_k]]

    query_embeddings = await _embed_texts([query])
    if not query_embeddings:
        return base_events[:top_k]
    query_embedding = query_embeddings[0]

    event_ids = [event["event_id"] for event in base_events]
    stored_embeddings = db.get_audit_embeddings(event_ids)
    missing_events = [event for event in base_events if event["event_id"] not in stored_embeddings]
    missing_events = missing_events[:30]
    if missing_events:
        texts = [_build_event_text(event) for event in missing_events]
        embeddings = await _embed_texts(texts)
        for event, embedding in zip(missing_events, embeddings):
            db.add_audit_embedding(event["event_id"], embedding)
            stored_embeddings[event["event_id"]] = embedding

    scored_events: List[Tuple[Dict[str, Any], float]] = []
    for event in base_events:
        embedding = stored_embeddings.get(event["event_id"])
        if embedding:
            score = _cosine_similarity(query_embedding, embedding)
        else:
            score = _score_keyword_match(query, event)
        scored_events.append((event, score))

    scored_events.sort(key=lambda item: (item[1], item[0].get("ts", 0)), reverse=True)
    return [event for event, _ in scored_events[:top_k]]


async def build_rag_answer(
    query: str,
    events: List[Dict[str, Any]],
) -> str:
    if not events:
        return "I couldn't find matching audit records for that query."

    safe_records = [_event_to_safe_record(event) for event in events]
    sources = " ".join([f"[#AID:{record['audit_id']}]" for record in safe_records])

    client = _get_chat_client()
    if client:
        prompt = (
            "You are a moderation audit assistant. Summarize the records for the admin. "
            "Only use the provided audit records. Do not invent details. "
            "Return a concise summary with citations like [#AID:xxxx]. "
            "Do not include raw message content."
        )
        records_block = json.dumps(safe_records, ensure_ascii=False, indent=2)
        try:
            response = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"Admin query: {query}\n\nAudit records:\n{records_block}",
                    },
                ],
                temperature=0.2,
                max_tokens=350,
            )
            content = response.choices[0].message.content or ""
            content = content.strip()
            if content:
                if "[#AID:" not in content:
                    content = f"{content}\n\nSources: {sources}"
                return content
        except Exception as exc:
            logger.warning("RAG answer generation failed: %s", exc)

    lines = ["🧾 <b>Audit summary</b>"]
    for record in safe_records:
        action = record.get("action")
        reason = record.get("reason") or "n/a"
        actor = record.get("actor_user_id")
        target = record.get("target_user_id") or "n/a"
        ts = record.get("ts")
        lines.append(
            f"• {ts} — {action} (actor {actor}, target {target}) — {reason} [#AID:{record['audit_id']}]"
        )
    lines.append(f"\nSources: {sources}")
    return "\n".join(lines)


def build_audit_detail(event: Dict[str, Any]) -> str:
    safe = _event_to_safe_record(event)
    lines = [
        "🧾 <b>Audit record detail</b>",
        f"ID: {safe['audit_id']}",
        f"Timestamp: {safe['ts']}",
        f"Action: {safe['action']}",
        f"Reason: {safe['reason'] or 'n/a'}",
        f"Actor user: {safe['actor_user_id']}",
        f"Target user: {safe['target_user_id'] or 'n/a'}",
    ]
    metadata = safe.get("metadata") or {}
    if metadata:
        lines.append("Metadata:")
        for key, value in metadata.items():
            lines.append(f"• {key}: {value}")
    return "\n".join(lines)
