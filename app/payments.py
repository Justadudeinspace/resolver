import logging
import secrets
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

INVOICE_TTL_SECONDS = 86400


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    stars: int
    resolves: int


@dataclass(frozen=True)
class GroupPlan:
    id: str
    name: str
    stars: int
    duration_days: Optional[int]


PLANS: Dict[str, Plan] = {
    "starter": Plan(id="starter", name="Starter", stars=5, resolves=1),
    "bundle": Plan(id="bundle", name="Bundle", stars=20, resolves=5),
    "pro": Plan(id="pro", name="Pro", stars=50, resolves=15),
}

GROUP_PLANS: Dict[str, GroupPlan] = {
    "group_monthly": GroupPlan(id="group_monthly", name="Monthly", stars=20, duration_days=30),
    "group_yearly": GroupPlan(id="group_yearly", name="Yearly", stars=100, duration_days=365),
    "group_lifetime": GroupPlan(id="group_lifetime", name="Lifetime", stars=500, duration_days=None),
}


def generate_invoice_id() -> str:
    return secrets.token_hex(8)


def build_group_plan_key(plan_id: str, group_id: int) -> str:
    return f"group:{plan_id}:{group_id}"


def parse_group_plan_key(plan_key: str) -> Optional[Dict[str, Any]]:
    if not plan_key.startswith("group:"):
        return None
    try:
        _, plan_id, group_id = plan_key.split(":", 2)
        return {"plan_id": plan_id, "group_id": int(group_id)}
    except (ValueError, TypeError):
        logger.warning("Invalid group plan key: %s", plan_key)
        return None
