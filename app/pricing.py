from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class PersonalPlan:
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


@dataclass(frozen=True)
class RagAddonPlan:
    id: str
    name: str
    stars: int
    duration_days: Optional[int]


PERSONAL_PLANS: Dict[str, PersonalPlan] = {
    "personal_monthly": PersonalPlan(id="personal_monthly", name="Monthly", stars=50, resolves=1),
    "personal_yearly": PersonalPlan(id="personal_yearly", name="Yearly", stars=450, resolves=5),
    "personal_lifetime": PersonalPlan(id="personal_lifetime", name="Lifetime", stars=1000, resolves=15),
}

GROUP_PLANS: Dict[str, GroupPlan] = {
    "group_monthly": GroupPlan(id="group_monthly", name="Monthly", stars=150, duration_days=30),
    "group_yearly": GroupPlan(id="group_yearly", name="Yearly", stars=1500, duration_days=365),
    "group_charter": GroupPlan(id="group_charter", name="Charter", stars=4000, duration_days=None),
}

RAG_ADDON_PLANS: Dict[str, RagAddonPlan] = {
    "rag_monthly": RagAddonPlan(id="rag_monthly", name="RAG Monthly Add-On", stars=50, duration_days=30),
}
