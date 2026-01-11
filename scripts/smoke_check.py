import asyncio
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.db import DB
from app.handlers import router
from app.llm import get_llm_client


def _validate_responses(responses: List[str]) -> None:
    if not isinstance(responses, list) or len(responses) != 3:
        raise ValueError("LLM response did not return exactly 3 options")
    if not all(isinstance(resp, str) and resp.strip() for resp in responses):
        raise ValueError("LLM response contains empty option")


async def _run_checks() -> None:
    db = DB(settings.db_path)
    if not db.health_check():
        raise RuntimeError("Database health check failed")

    if router is None:
        raise RuntimeError("Handlers router failed to initialize")

    responses = await get_llm_client().generate_responses(
        "stabilize",
        "I need a calm response for a tense conversation.",
        None,
    )
    _validate_responses(responses)


def main() -> int:
    try:
        asyncio.run(_run_checks())
    except Exception as exc:  # noqa: BLE001 - keep broad to surface any failure
        print(f"Smoke check failed: {exc}")
        return 1

    print("Smoke check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
