import logging
import secrets
from typing import Any, Optional

logger = logging.getLogger(__name__)

INVOICE_TTL_SECONDS = 86400


def generate_invoice_id() -> str:
    return secrets.token_hex(8)


def build_group_plan_key(plan_id: str, group_id: int) -> str:
    return f"group:{plan_id}:{group_id}"


def build_personal_plan_key(plan_id: str) -> str:
    return f"personal:{plan_id}"


def build_rag_plan_key(plan_id: str, group_id: int) -> str:
    return f"rag:{plan_id}:{group_id}"


def parse_group_plan_key(plan_key: str) -> Optional[Dict[str, Any]]:
    if not plan_key.startswith("group:"):
        return None
    try:
        _, plan_id, group_id = plan_key.split(":", 2)
        return {"plan_id": plan_id, "group_id": int(group_id)}
    except (ValueError, TypeError):
        logger.warning("Invalid group plan key: %s", plan_key)
        return None


def parse_personal_plan_key(plan_key: str) -> Optional[str]:
    if not plan_key.startswith("personal:"):
        return None
    _, plan_id = plan_key.split(":", 1)
    return plan_id or None


def parse_rag_plan_key(plan_key: str) -> Optional[Dict[str, Any]]:
    if not plan_key.startswith("rag:"):
        return None
    try:
        _, plan_id, group_id = plan_key.split(":", 2)
        return {"plan_id": plan_id, "group_id": int(group_id)}
    except (ValueError, TypeError):
        logger.warning("Invalid RAG plan key: %s", plan_key)
        return None
