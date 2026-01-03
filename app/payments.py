import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional

from .config import settings

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 32
PAYLOAD_TTL_SECONDS = 3600


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    stars: int
    resolves: int


PLANS: Dict[str, Plan] = {
    "starter": Plan(id="starter", name="Starter", stars=5, resolves=1),
    "bundle": Plan(id="bundle", name="Bundle", stars=20, resolves=5),
    "pro": Plan(id="pro", name="Pro", stars=50, resolves=15),
}


def _secret_ready() -> bool:
    return bool(settings.invoice_secret and len(settings.invoice_secret) >= MIN_SECRET_LENGTH)


def _sign(payload: str) -> Optional[str]:
    if not _secret_ready():
        logger.error("INVOICE_SECRET is missing or too short; payments disabled")
        return None
    key = settings.invoice_secret.encode("utf-8")
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_payload_base(uid: int, plan_id: str, timestamp: int, nonce: str) -> str:
    return f"uid={uid}|plan={plan_id}|ts={timestamp}|nonce={nonce}"


def create_invoice_payload(uid: int, plan_id: str) -> Optional[str]:
    if not _secret_ready():
        logger.error("INVOICE_SECRET must be at least %s characters", MIN_SECRET_LENGTH)
        return None

    plan = PLANS.get(plan_id)
    if not plan:
        logger.error("Unknown plan ID: %s", plan_id)
        return None

    ts = int(time.time())
    nonce = secrets.token_hex(8)
    payload_base = _build_payload_base(uid, plan_id, ts, nonce)
    sig = _sign(payload_base)
    if not sig:
        return None

    return f"{payload_base}|sig={sig}"


def verify_and_parse_payload(payload: str) -> Optional[Dict[str, Any]]:
    if not _secret_ready():
        logger.error("INVOICE_SECRET missing; cannot verify payload")
        return None

    try:
        parts = payload.split("|")
        data: Dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            data[key] = value

        if not {"uid", "plan", "ts", "nonce", "sig"}.issubset(data.keys()):
            logger.warning("Invalid payload structure")
            return None

        uid = int(data["uid"])
        plan_id = data["plan"]
        timestamp = int(data["ts"])
        nonce = data["nonce"]

        if plan_id not in PLANS:
            logger.warning("Unknown plan in payload")
            return None

        now = int(time.time())
        if timestamp <= 0 or timestamp > now + 60 or (now - timestamp) > PAYLOAD_TTL_SECONDS:
            logger.warning("Expired or invalid timestamp in payload")
            return None

        if not nonce:
            logger.warning("Missing nonce in payload")
            return None

        payload_base = _build_payload_base(uid, plan_id, timestamp, nonce)
        expected = _sign(payload_base)
        if not expected or not hmac.compare_digest(expected, data["sig"]):
            logger.warning("Invalid payload signature")
            return None

        return {"uid": uid, "plan": plan_id, "ts": timestamp}
    except (ValueError, TypeError) as exc:
        logger.warning("Payload parse error: %s", exc)
        return None


def verify_stars_payment(total_stars: int, payload: str) -> Optional[Dict[str, Any]]:
    data = verify_and_parse_payload(payload)
    if not data:
        return None

    plan = PLANS.get(data["plan"])
    if not plan or plan.stars != total_stars:
        logger.warning("Stars amount mismatch")
        return None

    return data
