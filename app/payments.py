import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional

from .config import settings

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    stars: int
    resolves: int


PLANS: Dict[str, Plan] = {
    "p1": Plan(id="p1", name="1 Resolve", stars=5, resolves=1),
    "p5": Plan(id="p5", name="5 Resolves", stars=20, resolves=5),
    "p15": Plan(id="p15", name="15 Resolves", stars=50, resolves=15),
}

STARS_TO_PLAN_ID: Dict[int, str] = {plan.stars: plan_id for plan_id, plan in PLANS.items()}


def _secret_ready() -> bool:
    return bool(settings.invoice_secret and len(settings.invoice_secret) >= MIN_SECRET_LENGTH)


def _sign(msg: bytes) -> Optional[str]:
    if not _secret_ready():
        logger.error("INVOICE_SECRET is missing or too short; payments disabled")
        return None
    key = settings.invoice_secret.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _encode_data(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def make_payload(user_id: int, plan_id: str) -> Optional[str]:
    plan = PLANS.get(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan ID: {plan_id}")

    payload = {
        "v": 1,
        "uid": user_id,
        "plan": plan.id,
        "stars": plan.stars,
        "res": plan.resolves,
        "ts": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    raw = _encode_data(payload)
    sig = _sign(raw)
    if not sig:
        return None

    pack = {"d": base64.urlsafe_b64encode(raw).decode("ascii"), "s": sig}
    return json.dumps(pack, separators=(",", ":"))


def verify_and_parse_payload(payload: str) -> Optional[Dict[str, Any]]:
    try:
        if not _secret_ready():
            logger.error("INVOICE_SECRET missing; cannot verify payload")
            return None

        pack = json.loads(payload)
        if "d" not in pack or "s" not in pack:
            logger.warning("Invalid payload structure")
            return None

        raw = base64.urlsafe_b64decode(pack["d"].encode("ascii"))
        sig = pack["s"]

        expected = _sign(raw)
        if not expected or not hmac.compare_digest(expected, sig):
            logger.warning("Invalid payload signature")
            return None

        data = json.loads(raw.decode("utf-8"))
        required_fields = ("v", "uid", "plan", "stars", "res", "ts", "nonce")
        for field in required_fields:
            if field not in data:
                logger.warning("Missing required field: %s", field)
                return None

        plan = PLANS.get(data["plan"])
        if not plan:
            logger.warning("Unknown plan: %s", data["plan"])
            return None

        if int(data["stars"]) != plan.stars or int(data["res"]) != plan.resolves:
            logger.warning("Plan data mismatch")
            return None

        timestamp = int(data.get("ts", 0))
        if timestamp <= 0:
            logger.warning("Invalid timestamp in payload")
            return None

        return data
    except json.JSONDecodeError as exc:
        logger.warning("JSON decode error: %s", exc)
        return None
    except (base64.binascii.Error, ValueError) as exc:
        logger.warning("Payload decode error: %s", exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error verifying payload: %s", exc)
        return None


def get_plan_by_stars(stars: int) -> Optional[Plan]:
    plan_id = STARS_TO_PLAN_ID.get(stars)
    return PLANS.get(plan_id) if plan_id else None


def create_invoice_payload(user_id: int, plan_id: str) -> Optional[str]:
    if not _secret_ready():
        logger.error("INVOICE_SECRET must be at least %s characters", MIN_SECRET_LENGTH)
        return None

    try:
        return make_payload(user_id, plan_id)
    except Exception as exc:
        logger.error("Failed to create invoice payload: %s", exc)
        return None


def verify_stars_payment(stars: int, payload: str) -> Optional[Dict[str, Any]]:
    data = verify_and_parse_payload(payload)
    if not data:
        return None

    if int(data["stars"]) != stars:
        logger.warning("Stars amount mismatch: %s != %s", data["stars"], stars)
        return None

    return data
