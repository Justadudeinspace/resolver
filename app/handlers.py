import json
import logging
import re
import time
import traceback
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .db import DB
from .languages import (
    LANGUAGE_LABELS,
    LANGUAGE_MODE_LABELS,
    LANGUAGE_MODE_DESCRIPTIONS,
    SUPPORTED_LANGUAGES,
)
from .llm import get_llm_client
from .payments import (
    INVOICE_TTL_SECONDS,
    build_group_plan_key,
    build_personal_plan_key,
    build_rag_plan_key,
    generate_invoice_id,
    parse_group_plan_key,
    parse_personal_plan_key,
    parse_rag_plan_key,
)
from .pricing import GROUP_PLANS, PERSONAL_PLANS, RAG_ADDON_PLANS
from .states import Flow
from .texts import (
    START_TEXT,
    GOAL_PROMPTS,
    PRICING_TEXT,
    HELP_TEXT,
    ACCOUNT_TEMPLATE,
    ERROR_MESSAGES,
    EMOJIS,
    GOAL_DESCRIPTIONS,
    SETTINGS_TEXT,
    SETTINGS_STATUS,
    SETTINGS_V2_DISABLED,
    BOT_COMMANDS,
    render_options,
    FEEDBACK_PROMPT,
    FEEDBACK_THANKS,
)
from .rag import (
    retrieve_audit_events,
    build_rag_answer,
    build_audit_detail,
    RAG_WINDOWS,
    RAG_ACTION_FILTERS,
)

logger = logging.getLogger(__name__)
router = Router()

STYLE_OPTIONS = {
    "neutral": "🙂 Neutral",
    "softer": "🫧 Softer",
    "firmer": "🧱 Firmer",
    "shorter": "✂️ Shorter",
}

LANGUAGE_MODES = ["clean", "adult", "unrestricted"]

INSULT_WORDS = {"idiot", "moron", "stupid", "dumb", "loser"}
PROFANITY_WORDS = {"fuck", "shit", "bitch", "asshole", "bastard"}
SLUR_WORDS = {"fag", "kike", "chink", "nigger", "tranny"}
SELF_HARM_TAUNTS = {"kys", "kill yourself", "end yourself", "go die"}

FLOOD_LIMIT = 5
FLOOD_WINDOW_SECONDS = 10
_flood_tracker = defaultdict(lambda: deque(maxlen=FLOOD_LIMIT * 2))
_group_entitlement_notice_ts: dict[int, int] = {}

GROUP_TEMPLATES = {
    "deescalate": "Let’s keep this respectful and calm so everyone feels safe to talk.",
    "deescalate_flood": "Please slow down and give others space to respond.",
    "warn": "⚠️ Please keep the conversation respectful. Continued escalation may lead to a temporary mute.",
    "mute": "🔇 Temporarily muting to cool things down (10 minutes).",
    "permission": "⚠️ I need permission to mute members. Admins, please enable the restriction permission.",
    "notify_admins": "Admins notified: moderation action taken.",
}

WELCOME_MAX_LENGTH = 2000
RULES_MAX_LENGTH = 4000
SECURITY_DEFAULTS = {
    "anti_link": False,
    "anti_spam": False,
    "mute_seconds": 600,
    "max_warnings": 3,
}
SECURITY_MUTE_RANGE = (1, 86400)
SECURITY_WARNING_RANGE = (1, 20)


def _create_invoice_record(
    db: DB,
    user_id: int,
    plan_id: str,
    amount: int,
    currency: str = "XTR",
) -> Optional[str]:
    for _ in range(3):
        invoice_id = generate_invoice_id()
        if db.create_invoice(
            invoice_id=invoice_id,
            user_id=user_id,
            plan_id=plan_id,
            amount=amount,
            currency=currency,
        ):
            return invoice_id
    return None


def _should_allow_xtr_amount(plan_id: str, plan_stars: int, amount: int, currency: str) -> bool:
    if currency == "XTR" and plan_stars < 1000 and amount >= 1000:
        logger.warning(
            "Pricing misconfigured: plan_id=%s plan_stars=%s amount=%s currency=%s",
            plan_id,
            plan_stars,
            amount,
            currency,
        )
        return False
    return True


def _amount_from_total(total_amount: int, currency: str) -> int:
    if currency == "XTR":
        return total_amount
    return total_amount // 100


async def _pre_checkout_fail(pre_checkout_query: PreCheckoutQuery, reason: str) -> None:
    logger.info(
        "Pre-checkout validation: ok=%s payload=%s reason=%s",
        False,
        pre_checkout_query.invoice_payload,
        reason,
    )
    await pre_checkout_query.answer(ok=False, error_message=reason)


async def _edit_or_send(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error("Failed to send fallback message:\n%s", traceback.format_exc())
    except Exception:
        logger.error("Failed to edit message:\n%s", traceback.format_exc())
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error("Failed to send fallback message:\n%s", traceback.format_exc())


async def _edit_message(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning("Failed to edit message: %s", exc)
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error("Failed to send fallback message:\n%s", traceback.format_exc())
    except Exception:
        logger.error("Failed to edit message:\n%s", traceback.format_exc())
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error("Failed to send fallback message:\n%s", traceback.format_exc())


def _fallback_notice() -> str:
    return (
        "\n\n⚠️ <b>AI mode is disabled.</b> "
        "Replies are generated using safe fallback templates. "
        "Set USE_LLM=true and OPENAI_API_KEY in .env to enable AI."
    )


def _maybe_add_fallback(text: str) -> str:
    return text + _fallback_notice() if not settings.use_llm else text


async def is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in {"administrator", "creator"}
    except Exception:
        return False


async def _bot_can_restrict(bot: Bot, chat_id: int) -> bool:
    try:
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if bot_member.status != "administrator":
            return False
        return bool(getattr(bot_member, "can_restrict_members", False))
    except Exception:
        return False


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 10:
        return 0.0
    uppercase = sum(1 for c in letters if c.isupper())
    return uppercase / max(1, len(letters))


def _contains_word(text: str, words: set) -> bool:
    for word in words:
        if " " in word:
            if word in text:
                return True
        elif re.search(rf"\b{re.escape(word)}\b", text):
            return True
    return False


def detect_trigger(text: str) -> str:
    lowered = text.lower()
    if _caps_ratio(text) > 0.7:
        return "caps"
    if re.search(r"[!?]{3,}", text):
        return "punctuation"
    if _contains_word(lowered, SELF_HARM_TAUNTS):
        return "self-harm-taunt"
    if _contains_word(lowered, SLUR_WORDS):
        return "slur"
    if _contains_word(lowered, PROFANITY_WORDS):
        return "profanity"
    if _contains_word(lowered, INSULT_WORDS):
        return "insult"
    return ""


def detect_flood(group_id: int, user_id: int, ts: int) -> bool:
    key = (group_id, user_id)
    window = _flood_tracker[key]
    window.append(ts)
    while window and ts - window[0] > FLOOD_WINDOW_SECONDS:
        window.popleft()
    return len(window) > FLOOD_LIMIT


def require_group_entitlement(db: DB, group_id: int) -> bool:
    try:
        return db.group_subscription_active(group_id)
    except Exception as exc:
        logger.error("Group entitlement check failed for %s: %s", group_id, exc)
        return False


def require_group_rag_entitlement(db: DB, group_id: int) -> bool:
    try:
        return db.group_subscription_active(group_id) and db.group_rag_subscription_active(group_id)
    except Exception as exc:
        logger.error("Group RAG entitlement check failed for %s: %s", group_id, exc)
        return False


async def _maybe_notify_group_entitlement(bot: Bot, group_id: int) -> None:
    now = int(time.time())
    last_notice = _group_entitlement_notice_ts.get(group_id, 0)
    if now - last_notice < 3600:
        return
    try:
        await bot.send_message(
            chat_id=group_id,
            text="⚠️ Admins: group moderation is disabled until a subscription is active.",
        )
        _group_entitlement_notice_ts[group_id] = now
    except Exception as exc:
        logger.warning("Failed to notify group %s about subscription status: %s", group_id, exc)


def _format_expiry(end_ts: Optional[int], plan_id: Optional[str]) -> str:
    plan = GROUP_PLANS.get(plan_id or "")
    if not plan:
        return "N/A"
    if plan.duration_days is None:
        return "One-time, non-refundable, lifetime access"
    if not end_ts:
        return "Unknown"
    return datetime.utcfromtimestamp(end_ts).strftime("%Y-%m-%d")


def _format_rag_expiry(end_ts: Optional[int]) -> str:
    if not end_ts:
        return "Unknown"
    return datetime.utcfromtimestamp(end_ts).strftime("%Y-%m-%d")


def _format_plan_label(plan_id: Optional[str]) -> str:
    plan = GROUP_PLANS.get(plan_id or "")
    if not plan:
        return "None"
    if plan.duration_days is None:
        return f"{plan.name} (one-time, non-refundable, lifetime access)"
    return f"{plan.name} ({plan.stars} Stars)"


def render_groupadmin_text(
    group: dict,
    subscription_info: dict,
    rag_subscription_info: dict,
    feature_enabled: bool,
) -> str:
    if not feature_enabled:
        return (
            "⚠️ <b>V2 Groups are disabled.</b>\n"
            "Group moderation and subscriptions are unavailable.\n"
        )
    language = group.get("language", "en")
    mode = group.get("language_mode", "clean")
    language_label = LANGUAGE_LABELS.get(language, language)
    mode_label = LANGUAGE_MODE_LABELS.get(mode, mode.title())
    subscription_status = "Active" if subscription_info.get("active") else "Inactive"
    rag_status = "Active" if rag_subscription_info.get("active") else "Inactive"
    plan_label = _format_plan_label(subscription_info.get("plan_id"))
    expires = _format_expiry(subscription_info.get("end_ts"), subscription_info.get("plan_id"))
    return (
        "🛡️ <b>Group Admin Panel</b>\n\n"
        f"Moderation enabled: {'On' if group.get('enabled') else 'Off'}\n"
        f"Language: {language_label} ({language})\n"
        f"Language mode: {mode_label}\n"
        f"Warn threshold: {group.get('warn_threshold')}\n"
        f"Mute threshold: {group.get('mute_threshold')}\n"
        f"Welcome: {'On' if group.get('welcome_enabled') else 'Off'}\n"
        f"Rules: {'On' if group.get('rules_enabled') else 'Off'}\n"
        f"Security: {'On' if group.get('security_enabled') else 'Off'}\n\n"
        f"Subscription: {subscription_status}\n"
        f"Plan: {plan_label}\n"
        f"Expires: {expires}\n"
        f"RAG Add-On: {rag_status}"
    )


def _subscription_required_notice() -> str:
    return "⚠️ Group moderation requires an active subscription."


def _rag_required_notice() -> str:
    return "⚠️ RAG requires an active group subscription and the RAG Add-On."


async def _require_group_entitlement_cb(cb: CallbackQuery, db: DB, group_id: int) -> bool:
    if require_group_entitlement(db, group_id):
        return True
    await cb.answer(_subscription_required_notice(), show_alert=True)
    return False


async def _require_group_entitlement_msg(msg: Message, db: DB) -> bool:
    if require_group_entitlement(db, msg.chat.id):
        return True
    await msg.answer(_subscription_required_notice())
    return False


async def _require_group_rag_entitlement_cb(cb: CallbackQuery, db: DB, group_id: int) -> bool:
    if require_group_rag_entitlement(db, group_id):
        return True
    await cb.answer(_rag_required_notice(), show_alert=True)
    return False


async def _require_group_rag_entitlement_msg(msg: Message, db: DB) -> bool:
    if require_group_rag_entitlement(db, msg.chat.id):
        return True
    await msg.answer(_rag_required_notice())
    return False


def _parse_security_config(raw_config: Optional[str]) -> dict:
    config = SECURITY_DEFAULTS.copy()
    if not raw_config:
        return config
    try:
        data = json.loads(raw_config)
    except json.JSONDecodeError:
        return config
    if not isinstance(data, dict):
        return config
    if "anti_link" in data:
        config["anti_link"] = bool(data["anti_link"])
    if "anti_spam" in data:
        config["anti_spam"] = bool(data["anti_spam"])
    if "mute_seconds" in data:
        try:
            config["mute_seconds"] = int(data["mute_seconds"])
        except (TypeError, ValueError):
            pass
    if "max_warnings" in data:
        try:
            config["max_warnings"] = int(data["max_warnings"])
        except (TypeError, ValueError):
            pass
    return config


def _render_security_settings_text(config: dict) -> str:
    return (
        "🛡 <b>Security Settings</b>\n\n"
        f"Anti-link: {'On' if config['anti_link'] else 'Off'}\n"
        f"Anti-spam: {'On' if config['anti_spam'] else 'Off'}\n"
        f"Mute seconds: {config['mute_seconds']}\n"
        f"Max warnings: {config['max_warnings']}\n\n"
        "Use the buttons below to update the configuration."
    )


def _group_plan_button_text(plan_id: str, fallback: str) -> str:
    plan = GROUP_PLANS.get(plan_id)
    if plan:
        if plan.duration_days is None:
            return f"⭐ {plan.name} — {plan.stars} Stars (one-time, non-refundable, lifetime access)"
        return f"⭐ {plan.name} — {plan.stars} Stars"
    addon = RAG_ADDON_PLANS.get(plan_id)
    if addon:
        return f"⭐ {addon.name} — {addon.stars} Stars"
    return fallback


def kb_groupadmin(group: dict, subscription_info: dict, rag_subscription_info: dict, feature_enabled: bool):
    b = InlineKeyboardBuilder()
    if not feature_enabled:
        b.button(text=f"{EMOJIS['back']} Close", callback_data="ga:menu:close")
        b.adjust(1)
        return b.as_markup()

    enabled = bool(group.get("enabled"))
    b.button(
        text="✅ Enable" if not enabled else "🚫 Disable",
        callback_data="ga:toggle_enabled",
    )
    b.button(text="🔎 RAG Query", callback_data="ga:menu:rag")
    b.button(text="🌐 Language", callback_data="ga:menu:language")
    b.button(text="🧭 Mode", callback_data="ga:menu:mode")
    b.button(text=f"Warn threshold ({group.get('warn_threshold')})", callback_data="ga:menu:warn")
    b.button(text=f"Mute threshold ({group.get('mute_threshold')})", callback_data="ga:menu:mute")
    b.button(text="✍️ Set Welcome Message", callback_data="ga:menu:set_welcome")
    b.button(text="📜 Set Rules", callback_data="ga:menu:set_rules")
    b.button(text="🛡 Set Security Settings", callback_data="ga:menu:security")
    b.button(
        text=f"Welcome {'On' if group.get('welcome_enabled') else 'Off'}",
        callback_data="ga:toggle:welcome_enabled",
    )
    b.button(
        text=f"Rules {'On' if group.get('rules_enabled') else 'Off'}",
        callback_data="ga:toggle:rules_enabled",
    )
    b.button(
        text=f"Security {'On' if group.get('security_enabled') else 'Off'}",
        callback_data="ga:toggle:security_enabled",
    )

    if not subscription_info.get("active"):
        b.button(
            text=_group_plan_button_text("group_monthly", "⭐ Buy Monthly"),
            callback_data="ga:buy:group_monthly",
        )
        b.button(
            text=_group_plan_button_text("group_yearly", "⭐ Buy Yearly"),
            callback_data="ga:buy:group_yearly",
        )
        b.button(
            text=_group_plan_button_text("group_charter", "⭐ Buy Charter"),
            callback_data="ga:buy:group_charter",
        )
    elif not rag_subscription_info.get("active"):
        b.button(
            text=_group_plan_button_text("rag_monthly", "⭐ Buy RAG Add-On"),
            callback_data="ga:buy:rag_monthly",
        )
    b.button(text=f"{EMOJIS['back']} Close", callback_data="ga:menu:close")
    b.adjust(2, 2, 2, 2, 2, 2, 2, 2, 1)
    return b.as_markup()


def kb_group_rag_menu(window_key: str, filter_key: str):
    b = InlineKeyboardBuilder()
    window_labels = {
        "24h": "🕒 Last 24h",
        "7d": "📅 Last 7d",
    }
    filter_labels = {
        "incidents": "🚨 Incidents",
        "mutes": "🔇 Mutes",
        "warnings": "⚠️ Warnings",
    }
    for key in ("24h", "7d"):
        prefix = "✅ " if window_key == key else ""
        b.button(text=f"{prefix}{window_labels[key]}", callback_data=f"ga:rag:window:{key}")
    for key in ("incidents", "mutes", "warnings"):
        prefix = "✅ " if filter_key == key else ""
        b.button(text=f"{prefix}{filter_labels[key]}", callback_data=f"ga:rag:filter:{key}")
    b.button(text="❓ Ask a question", callback_data="ga:rag:ask")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.adjust(2, 3, 1, 1)
    return b.as_markup()


def kb_group_language_menu():
    b = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGUAGES:
        label = LANGUAGE_LABELS.get(code, code)
        b.button(text=f"{label} ({code})", callback_data=f"ga:lang:{code}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.adjust(2)
    return b.as_markup()


def kb_group_mode_menu():
    b = InlineKeyboardBuilder()
    for mode in LANGUAGE_MODES:
        label = LANGUAGE_MODE_LABELS.get(mode, mode.title())
        b.button(text=f"{label}", callback_data=f"ga:mode:{mode}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.adjust(1)
    return b.as_markup()


def kb_group_threshold_menu(threshold_type: str):
    b = InlineKeyboardBuilder()
    if threshold_type == "warn":
        options = range(1, 6)
    else:
        options = range(2, 7)
    for value in options:
        b.button(text=str(value), callback_data=f"ga:{threshold_type}:{value}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.adjust(3, 3, 1)
    return b.as_markup()


def kb_group_text_prompt():
    b = InlineKeyboardBuilder()
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.button(text="Cancel", callback_data="ga:flow:cancel")
    b.adjust(2)
    return b.as_markup()


def kb_group_security_menu(config: dict):
    b = InlineKeyboardBuilder()
    link_prefix = "✅" if config.get("anti_link") else "❌"
    spam_prefix = "✅" if config.get("anti_spam") else "❌"
    b.button(text=f"{link_prefix} Anti-link", callback_data="ga:security:toggle:anti_link")
    b.button(text=f"{spam_prefix} Anti-spam", callback_data="ga:security:toggle:anti_spam")
    b.button(
        text=f"⏱ Mute seconds ({config.get('mute_seconds')})",
        callback_data="ga:security:set:mute_seconds",
    )
    b.button(
        text=f"⚠️ Max warnings ({config.get('max_warnings')})",
        callback_data="ga:security:set:max_warnings",
    )
    b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:main")
    b.button(text="Cancel", callback_data="ga:flow:cancel")
    b.adjust(2, 2, 2)
    return b.as_markup()


def kb_goals():
    b = InlineKeyboardBuilder()

    for goal_key, goal_desc in GOAL_DESCRIPTIONS.items():
        b.button(
            text=f"{goal_desc['emoji']} {goal_desc['name']}",
            callback_data=f"goal:{goal_key}",
        )

    b.button(text=f"{EMOJIS['buy']} Pricing", callback_data="nav:pricing")
    b.button(text=f"{EMOJIS['account']} Account", callback_data="nav:account")
    b.button(text=f"{EMOJIS['settings']} Settings", callback_data="nav:settings")
    b.button(text=f"{EMOJIS['help']} Help", callback_data="nav:help")
    b.button(text=f"{EMOJIS['feedback']} Send feedback", callback_data="feedback:start")
    b.adjust(3, 3, 2)
    return b.as_markup()


def kb_back_main():
    b = InlineKeyboardBuilder()
    b.button(text=f"{EMOJIS['back']} Back to main menu", callback_data="nav:goals")
    b.adjust(1)
    return b.as_markup()


def kb_after_result():
    b = InlineKeyboardBuilder()
    b.button(text=f"{EMOJIS['retry']} Retry", callback_data="retry:menu")
    b.button(text=f"{EMOJIS['buy']} Get more", callback_data="nav:pricing")
    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(2, 1)
    return b.as_markup()


def kb_pricing():
    b = InlineKeyboardBuilder()
    monthly = PERSONAL_PLANS["personal_monthly"]
    yearly = PERSONAL_PLANS["personal_yearly"]
    lifetime = PERSONAL_PLANS["personal_lifetime"]
    b.button(text=f"⭐ Personal Monthly — {monthly.stars} Stars", callback_data="buy:personal_monthly")
    b.button(text=f"⭐ Personal Yearly — {yearly.stars} Stars", callback_data="buy:personal_yearly")
    b.button(text=f"⭐ Personal Lifetime — {lifetime.stars} Stars", callback_data="buy:personal_lifetime")
    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()


def kb_retry_menu():
    b = InlineKeyboardBuilder()
    b.button(text="Softer", callback_data="retry:softer")
    b.button(text="Firmer", callback_data="retry:firmer")
    b.button(text="Shorter", callback_data="retry:shorter")
    b.button(text="Cancel", callback_data="nav:goals")
    b.adjust(3, 1)
    return b.as_markup()


def kb_settings(user: dict, v2_personal_enabled: bool):
    b = InlineKeyboardBuilder()
    for goal_key, goal_desc in GOAL_DESCRIPTIONS.items():
        b.button(
            text=f"{goal_desc['emoji']} {goal_desc['name']}",
            callback_data=f"settings:goal:{goal_key}",
        )
    b.button(text="❌ None", callback_data="settings:goal:none")

    for style_key, style_label in STYLE_OPTIONS.items():
        b.button(text=style_label, callback_data=f"settings:style:{style_key}")
    b.button(text="❌ None", callback_data="settings:style:none")

    if v2_personal_enabled:
        language_label = LANGUAGE_LABELS.get(user.get("language", "en"), "English")
        mode_label = LANGUAGE_MODE_LABELS.get(user.get("language_mode", "clean"), "Clean")
        b.button(text=f"🌐 Language: {language_label}", callback_data="settings:menu:language")
        b.button(text=f"🧭 Mode: {mode_label}", callback_data="settings:menu:mode")
    if settings.feature_v2_personal:
        toggle_label = "Disable" if v2_personal_enabled else "Enable"
        b.button(text=f"🧪 {toggle_label} V2 Personal", callback_data=f"settings:v2:{toggle_label.lower()}")

    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(3, 1, 2, 2, 2, 1, 1)
    return b.as_markup()


def kb_language_menu():
    b = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGUAGES:
        label = LANGUAGE_LABELS.get(code, code)
        b.button(text=f"{label} ({code})", callback_data=f"settings:lang:{code}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="settings:menu:main")
    b.adjust(2)
    return b.as_markup()


def kb_language_mode_menu():
    b = InlineKeyboardBuilder()
    for mode in LANGUAGE_MODES:
        label = LANGUAGE_MODE_LABELS.get(mode, mode.title())
        b.button(text=f"{label}", callback_data=f"settings:mode:{mode}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="settings:menu:main")
    b.adjust(1)
    return b.as_markup()


def render_settings_text(user: dict, v2_personal_enabled: bool) -> str:
    default_goal = user.get("default_goal")
    default_style = user.get("default_style")

    goal_label = (
        GOAL_DESCRIPTIONS[default_goal]["name"]
        if default_goal in GOAL_DESCRIPTIONS
        else "None"
    )
    style_label = STYLE_OPTIONS.get(default_style, "None")
    text = SETTINGS_TEXT + SETTINGS_STATUS.format(
        default_goal=goal_label, default_style=style_label
    )
    if not v2_personal_enabled:
        return SETTINGS_V2_DISABLED + text

    language = user.get("language", "en")
    mode = user.get("language_mode", "clean")
    language_label = LANGUAGE_LABELS.get(language, language)
    mode_label = LANGUAGE_MODE_LABELS.get(mode, mode.title())
    mode_desc = LANGUAGE_MODE_DESCRIPTIONS.get(mode, "")
    return (
        text
        + "\n"
        + f"Language: {language_label} ({language})\n"
        + f"Language mode: {mode_label}\n"
        + f"{mode_desc}\n"
    )


def kb_change_goal():
    b = InlineKeyboardBuilder()
    b.button(text="Change goal", callback_data="nav:goals")
    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(2)
    return b.as_markup()


def render_unknown_commands() -> str:
    lines = ["I didn't understand that. Try:"]
    for command, description in BOT_COMMANDS:
        lines.append(f"/{command} - {description}")
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext, db: DB):
    await state.clear()

    db.ensure_user(
        user_id=msg.from_user.id,
        username=msg.from_user.username,
        first_name=msg.from_user.first_name,
        last_name=msg.from_user.last_name,
    )

    await msg.answer(_maybe_add_fallback(START_TEXT), reply_markup=kb_goals())
    logger.info("User %s started the bot", msg.from_user.id)


@router.message(Command("resolve"))
async def cmd_resolve(msg: Message, state: FSMContext, db: DB):
    await state.clear()
    db.ensure_user(msg.from_user.id)
    user = db.get_user(msg.from_user.id)
    default_goal = user.get("default_goal")
    default_style = user.get("default_style")

    if default_goal in GOAL_DESCRIPTIONS:
        db.set_goal(msg.from_user.id, default_goal)
        db.set_retry_flags(msg.from_user.id, last_paid=False, free_retry=False)
        await state.set_state(Flow.waiting_for_text)
        style_line = ""
        if default_style in STYLE_OPTIONS:
            style_line = f"\nDefault tone: {STYLE_OPTIONS[default_style]}"
        await msg.answer(
            f"Default goal: {GOAL_DESCRIPTIONS[default_goal]['name']}{style_line}\n\n"
            f"{GOAL_PROMPTS[default_goal]}",
            reply_markup=kb_goals(),
        )
    else:
        await msg.answer("Choose a goal:", reply_markup=kb_goals())


@router.message(Command("pricing"))
async def cmd_pricing(msg: Message):
    await msg.answer(PRICING_TEXT, reply_markup=kb_pricing())


@router.message(Command("buy"))
async def cmd_buy(msg: Message, command: CommandObject, bot: Bot, db: DB):
    plan_id = (command.args or "").strip().lower()
    if plan_id in PERSONAL_PLANS:
        plan = PERSONAL_PLANS[plan_id]
        if not _should_allow_xtr_amount(plan.id, plan.stars, plan.stars, "XTR"):
            await msg.answer("Pricing misconfigured. Please contact admin.")
            return
        payload = _create_invoice_record(
            db=db,
            user_id=msg.from_user.id,
            plan_id=build_personal_plan_key(plan.id),
            amount=plan.stars,
        )
        if not payload:
            await msg.answer("Failed to create invoice. Please try again.")
            return

        logger.info(
            "Invoice created: plan_id=%s stars_amount=%s payload_len=%s",
            plan.id,
            plan.stars,
            len(payload),
        )
        prices = [LabeledPrice(label=f"{plan.resolves} Resolves (Personal)", amount=plan.stars)]
        try:
            await bot.send_invoice(
                chat_id=msg.from_user.id,
                title=f"Personal {plan.name} - The Resolver",
                description=f"Personal (DM) plan: {plan.resolves} resolve(s).",
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=prices,
                start_parameter="resolver_bot",
                need_email=False,
                need_name=False,
                need_phone_number=False,
                need_shipping_address=False,
                is_flexible=False,
                disable_notification=True,
                protect_content=False,
            )
            return
        except Exception as exc:
            logger.error("Failed to send invoice: %s", exc)
            await msg.answer("Failed to create invoice. Please try again.")
            return

    await msg.answer(PRICING_TEXT, reply_markup=kb_pricing())


@router.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(_maybe_add_fallback(HELP_TEXT), reply_markup=kb_back_main())


@router.message(Command("account"))
async def cmd_account(msg: Message, db: DB):
    user_id = msg.from_user.id
    db.ensure_user(user_id)

    user = db.get_user(user_id)
    stats = db.get_user_stats(user_id)

    free_status = "Available" if db.can_use_free_today(user_id) else "Used today"

    text = ACCOUNT_TEMPLATE.format(
        paid_resolves=user.get("resolves_remaining", 0),
        free_status=free_status,
        total_uses=stats.get("total_interactions", 0),
        account_age=stats.get("account_age_days", 0),
    )

    await msg.answer(text, reply_markup=kb_back_main())


@router.message(Command("settings"))
async def cmd_settings(msg: Message, db: DB):
    user_id = msg.from_user.id
    db.ensure_user(user_id)
    user = db.get_user(user_id)
    v2_personal_enabled = settings.feature_v2_personal and bool(user.get("v2_enabled"))
    await msg.answer(
        render_settings_text(user, v2_personal_enabled),
        reply_markup=kb_settings(user, v2_personal_enabled),
    )


@router.message(Command("feedback"))
async def cmd_feedback(msg: Message, command: CommandObject, state: FSMContext, db: DB):
    await state.clear()
    feedback = command.args
    if feedback:
        meta_json = json.dumps(
            {
                "source": "command",
                "chat_id": msg.chat.id,
                "message_id": msg.message_id,
            }
        )
        db.add_feedback(msg.from_user.id, feedback.strip(), meta_json)
        logger.info(
            "Feedback received from user %s (length=%s)",
            msg.from_user.id,
            len(feedback),
        )
        logger.debug("Feedback detail from user %s: %s", msg.from_user.id, feedback)
        await msg.answer(FEEDBACK_THANKS, reply_markup=kb_goals())
    else:
        await msg.answer(FEEDBACK_PROMPT, reply_markup=kb_back_main())
        await state.set_state(Flow.waiting_for_feedback)


@router.callback_query(F.data == "feedback:start")
async def feedback_start_handler(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_or_send(cb.message, FEEDBACK_PROMPT, reply_markup=kb_back_main())
    await state.set_state(Flow.waiting_for_feedback)
    await cb.answer()


@router.callback_query(F.data.startswith("nav:"))
async def nav_handler(cb: CallbackQuery, state: FSMContext, db: DB):
    action = cb.data.split(":", 1)[1]
    await state.clear()

    if action == "pricing":
        await _edit_or_send(cb.message, PRICING_TEXT, reply_markup=kb_pricing())
    elif action == "help":
        await _edit_or_send(cb.message, _maybe_add_fallback(HELP_TEXT), reply_markup=kb_back_main())
    elif action == "settings":
        user_id = cb.from_user.id
        db.ensure_user(user_id)
        user = db.get_user(user_id)
        v2_personal_enabled = settings.feature_v2_personal and bool(user.get("v2_enabled"))
        await _edit_or_send(
            cb.message,
            render_settings_text(user, v2_personal_enabled),
            reply_markup=kb_settings(user, v2_personal_enabled),
        )
    elif action == "account":
        user_id = cb.from_user.id
        db.ensure_user(user_id)
        user = db.get_user(user_id)
        stats = db.get_user_stats(user_id)
        free_status = "Available" if db.can_use_free_today(user_id) else "Used today"

        text = ACCOUNT_TEMPLATE.format(
            paid_resolves=user.get("resolves_remaining", 0),
            free_status=free_status,
            total_uses=stats.get("total_interactions", 0),
            account_age=stats.get("account_age_days", 0),
        )
        await _edit_or_send(cb.message, text, reply_markup=kb_back_main())
    else:
        await state.clear()
        await _edit_or_send(cb.message, "Choose a goal:", reply_markup=kb_goals())

    await cb.answer()


@router.callback_query(F.data.startswith("goal:"))
async def choose_goal(cb: CallbackQuery, state: FSMContext, db: DB):
    goal = cb.data.split(":", 1)[1]
    user_id = cb.from_user.id

    db.ensure_user(user_id)
    db.set_goal(user_id, goal)
    db.set_retry_flags(user_id, last_paid=False, free_retry=False)

    await state.set_state(Flow.waiting_for_text)
    await _edit_or_send(cb.message, GOAL_PROMPTS[goal], reply_markup=kb_back_main())
    await cb.answer()


@router.callback_query(F.data.startswith("settings:"))
async def settings_handler(cb: CallbackQuery, db: DB):
    _, setting, value = cb.data.split(":", 2)
    user_id = cb.from_user.id
    db.ensure_user(user_id)

    user = db.get_user(user_id)
    v2_personal_enabled = settings.feature_v2_personal and bool(user.get("v2_enabled"))

    if setting == "menu":
        if value == "language":
            if not v2_personal_enabled:
                await cb.answer("V2 personal is disabled.")
                return
            await _edit_or_send(
                cb.message,
                "Choose your language:",
                reply_markup=kb_language_menu(),
            )
            await cb.answer()
            return
        if value == "mode":
            if not v2_personal_enabled:
                await cb.answer("V2 personal is disabled.")
                return
            await _edit_or_send(
                cb.message,
                "Choose your language mode:",
                reply_markup=kb_language_mode_menu(),
            )
            await cb.answer()
            return
        if value == "main":
            await _edit_or_send(
                cb.message,
                render_settings_text(user, v2_personal_enabled),
                reply_markup=kb_settings(user, v2_personal_enabled),
            )
            await cb.answer()
            return
    if setting == "goal":
        goal_value = None if value == "none" else value
        if goal_value is not None and goal_value not in GOAL_DESCRIPTIONS:
            await cb.answer("Unknown goal option.")
            return
        db.set_default_goal(user_id, goal_value)
    elif setting == "style":
        style_value = None if value == "none" else value
        if style_value is not None and style_value not in STYLE_OPTIONS:
            await cb.answer("Unknown style option.")
            return
        db.set_default_style(user_id, style_value)
    elif setting == "v2":
        if not settings.feature_v2_personal:
            await cb.answer("V2 personal is disabled.")
            return
        enable_v2 = value == "enable"
        db.set_v2_enabled(user_id, enable_v2)
        user = db.get_user(user_id)
        v2_personal_enabled = settings.feature_v2_personal and bool(user.get("v2_enabled"))
    elif setting == "lang":
        if not v2_personal_enabled:
            await cb.answer("V2 personal is disabled.")
            return
        if value not in SUPPORTED_LANGUAGES:
            await cb.answer("Unknown language.")
            return
        db.set_language(user_id, value)
    elif setting == "mode":
        if not v2_personal_enabled:
            await cb.answer("V2 personal is disabled.")
            return
        if value not in LANGUAGE_MODES:
            await cb.answer("Unknown language mode.")
            return
        db.set_language_mode(user_id, value)
    else:
        await cb.answer("Unknown setting.")
        return

    user = db.get_user(user_id)
    await _edit_or_send(
        cb.message,
        render_settings_text(user, v2_personal_enabled),
        reply_markup=kb_settings(user, v2_personal_enabled),
    )
    await cb.answer("Settings saved.")


@router.message(Flow.waiting_for_text)
async def on_text_input(msg: Message, state: FSMContext, db: DB):
    if not msg.text:
        await msg.answer("Please send text so I can help.")
        return

    user_id = msg.from_user.id
    text = msg.text.strip()

    if len(text) > settings.max_input_length:
        await msg.answer(
            ERROR_MESSAGES["invalid_input"].format(max_length=settings.max_input_length)
        )
        return

    user = db.get_user(user_id)
    if not user:
        await state.clear()
        await msg.answer("Please start over: /start")
        return

    goal = user.get("current_goal", "").strip()
    if not goal:
        await state.clear()
        await msg.answer("Choose a goal first:", reply_markup=kb_goals())
        return

    default_style = user.get("default_style")
    modifier = default_style if default_style in STYLE_OPTIONS else None
    language = user.get("language", "en")
    language_mode = user.get("language_mode", "clean")

    if user.get("resolves_remaining", 0) > 0 and db.consume_paid_resolve(user_id):
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=True, free_retry=True)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await get_llm_client().generate_responses(
            goal,
            text,
            modifier,
            language=language,
            language_mode=language_mode,
        )
        db.log_interaction(user_id, goal, text, responses, used_paid=True)

        await typing_msg.delete()
        await msg.answer(render_options(*responses), reply_markup=kb_after_result())
        return

    if goal == "stabilize" and db.can_use_free_today(user_id):
        db.mark_free_used_today(user_id)
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=False, free_retry=False)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await get_llm_client().generate_responses(
            goal,
            text,
            modifier,
            language=language,
            language_mode=language_mode,
        )
        db.log_interaction(user_id, goal, text, responses, used_paid=False)

        await typing_msg.delete()
        await msg.answer(render_options(*responses), reply_markup=kb_after_result())
        return

    await msg.answer(ERROR_MESSAGES["no_resolves"], reply_markup=kb_pricing())


@router.message(Flow.waiting_for_feedback)
async def on_feedback_message(msg: Message, state: FSMContext, db: DB):
    if not msg.text:
        await msg.answer("Please send your feedback as text.")
        return

    feedback = msg.text.strip()
    if not feedback:
        await msg.answer("Please send your feedback as text.")
        return

    meta_json = json.dumps(
        {
            "source": "flow",
            "chat_id": msg.chat.id,
            "message_id": msg.message_id,
        }
    )
    db.add_feedback(msg.from_user.id, feedback, meta_json)
    logger.info(
        "Feedback received from user %s (length=%s)",
        msg.from_user.id,
        len(feedback),
    )
    logger.debug("Feedback detail from user %s: %s", msg.from_user.id, feedback)
    await state.clear()
    await msg.answer(FEEDBACK_THANKS, reply_markup=kb_goals())


@router.message(Command("groupadmin"))
async def cmd_groupadmin(msg: Message, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This command can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return

    group = db.get_group_settings(msg.chat.id)
    subscription_info = db.get_group_subscription_info(msg.chat.id)
    rag_subscription_info = db.get_group_rag_subscription_info(msg.chat.id)
    text = render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups)
    await msg.answer(
        text,
        reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
    )


@router.message(Command("grouplogs"))
async def cmd_grouplogs(msg: Message, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This command can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return
    if not settings.feature_v2_groups:
        await msg.answer("V2 groups are disabled.")
        return

    logs = db.get_group_logs(msg.chat.id, limit=20)
    if not logs:
        await msg.answer("No moderation logs yet.")
        return

    lines = ["🧾 <b>Moderation Logs</b> (last 20):\n"]
    for entry in logs:
        ts = datetime.utcfromtimestamp(entry["ts"]).strftime("%Y-%m-%d %H:%M")
        trigger = entry.get("trigger", "unknown")
        action = entry.get("action", "none")
        user_id = entry.get("user_id")
        lines.append(f"{ts} • user {user_id} • {trigger} → {action}")
    await msg.answer("\n".join(lines))


@router.callback_query(F.data.startswith("ga:"))
async def groupadmin_handler(cb: CallbackQuery, bot: Bot, db: DB, state: FSMContext):
    if not cb.message:
        await cb.answer()
        return

    if cb.message.chat.type not in {"group", "supergroup"}:
        await cb.answer("Group-only command.")
        return

    if not await is_group_admin(bot, cb.message.chat.id, cb.from_user.id):
        await cb.answer("This command is restricted to group admins.")
        return

    group_id = cb.message.chat.id
    group = db.get_group_settings(group_id)
    if not settings.feature_v2_groups and cb.data not in {"ga:menu:close"}:
        await _edit_message(
            cb.message,
            render_groupadmin_text({}, {}, {}, False),
            reply_markup=kb_groupadmin({}, {}, {}, False),
        )
        await cb.answer()
        return

    action = cb.data.split(":", 1)[1]

    if action == "menu:close":
        await _edit_message(cb.message, "Admin panel closed.")
        await cb.answer()
        return
    if action == "menu:main":
        await state.clear()
        subscription_info = db.get_group_subscription_info(group_id)
        rag_subscription_info = db.get_group_rag_subscription_info(group_id)
        await _edit_message(
            cb.message,
            render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
            reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        )
        await cb.answer()
        return
    if action == "flow:cancel":
        await state.clear()
        subscription_info = db.get_group_subscription_info(group_id)
        rag_subscription_info = db.get_group_rag_subscription_info(group_id)
        await _edit_message(
            cb.message,
            render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
            reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        )
        await cb.answer("Canceled.")
        return
    if action == "menu:language":
        await _edit_message(cb.message, "Choose group language:", reply_markup=kb_group_language_menu())
        await cb.answer()
        return
    if action == "menu:mode":
        await _edit_message(cb.message, "Choose group language mode:", reply_markup=kb_group_mode_menu())
        await cb.answer()
        return
    if action == "menu:warn":
        await _edit_message(
            cb.message,
            "Set warning threshold:",
            reply_markup=kb_group_threshold_menu("warn"),
        )
        await cb.answer()
        return
    if action == "menu:mute":
        await _edit_message(
            cb.message,
            "Set mute threshold:",
            reply_markup=kb_group_threshold_menu("mute"),
        )
        await cb.answer()
        return
    if action == "menu:rag":
        if not await _require_group_rag_entitlement_cb(cb, db, group_id):
            return
        data = await state.get_data()
        window_key = data.get("rag_window", "24h")
        filter_key = data.get("rag_filter", "incidents")
        await _edit_message(
            cb.message,
            "Ask a question about this group's moderation history.",
            reply_markup=kb_group_rag_menu(window_key, filter_key),
        )
        await cb.answer()
        return
    if action == "menu:set_welcome":
        if not await _require_group_entitlement_cb(cb, db, group_id):
            return
        await state.clear()
        await state.update_data(group_id=group_id)
        await state.set_state(Flow.waiting_for_welcome_message)
        await _edit_message(
            cb.message,
            "Send the welcome message text to save for this group.",
            reply_markup=kb_group_text_prompt(),
        )
        await cb.answer()
        return
    if action == "menu:set_rules":
        if not await _require_group_entitlement_cb(cb, db, group_id):
            return
        await state.clear()
        await state.update_data(group_id=group_id)
        await state.set_state(Flow.waiting_for_rules_text)
        await _edit_message(
            cb.message,
            "Send the rules text to save for this group.",
            reply_markup=kb_group_text_prompt(),
        )
        await cb.answer()
        return
    if action == "menu:security":
        if not await _require_group_entitlement_cb(cb, db, group_id):
            return
        await state.clear()
        config = _parse_security_config(group.get("security_config_json"))
        await _edit_message(
            cb.message,
            _render_security_settings_text(config),
            reply_markup=kb_group_security_menu(config),
        )
        await cb.answer()
        return
    if action.startswith("security:toggle:"):
        if not await _require_group_entitlement_cb(cb, db, group_id):
            return
        key = action.split(":", 2)[2]
        if key not in {"anti_link", "anti_spam"}:
            await cb.answer("Unknown security option.")
            return
        config = _parse_security_config(group.get("security_config_json"))
        config[key] = not bool(config.get(key))
        db.set_group_security_config(group_id, json.dumps(config))
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_update",
            reason="security_config",
            metadata={"field": key, "new": config[key]},
        )
        await _edit_message(
            cb.message,
            _render_security_settings_text(config),
            reply_markup=kb_group_security_menu(config),
        )
        await cb.answer("Updated.")
        return
    if action.startswith("security:set:"):
        if not await _require_group_entitlement_cb(cb, db, group_id):
            return
        field = action.split(":", 2)[2]
        if field not in {"mute_seconds", "max_warnings"}:
            await cb.answer("Unknown security option.")
            return
        await state.clear()
        await state.update_data(group_id=group_id, security_field=field)
        await state.set_state(Flow.waiting_for_security_value)
        prompt = "Send the mute duration in seconds." if field == "mute_seconds" else "Send the max warnings count."
        await _edit_message(cb.message, prompt, reply_markup=kb_group_text_prompt())
        await cb.answer()
        return
    if action == "toggle_enabled":
        current = bool(group.get("enabled"))
        db.set_group_enabled(group_id, not current)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_toggle",
            reason="enabled",
            metadata={"field": "enabled", "old": current, "new": not current},
        )
    elif action.startswith("toggle:"):
        field = action.split(":", 1)[1]
        current = bool(group.get(field))
        db.set_group_toggle(group_id, field, not current)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_toggle",
            reason=field,
            metadata={"field": field, "old": current, "new": not current},
        )
    elif action.startswith("lang:"):
        language = action.split(":", 1)[1]
        if language not in SUPPORTED_LANGUAGES:
            await cb.answer("Unknown language.")
            return
        db.set_group_language(group_id, language)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_update",
            reason="language",
            metadata={"field": "language", "old": group.get("language"), "new": language},
        )
    elif action.startswith("mode:"):
        mode = action.split(":", 1)[1]
        if mode not in LANGUAGE_MODES:
            await cb.answer("Unknown mode.")
            return
        db.set_group_language_mode(group_id, mode)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_update",
            reason="language_mode",
            metadata={"field": "language_mode", "old": group.get("language_mode"), "new": mode},
        )
    elif action.startswith("warn:"):
        try:
            value = int(action.split(":", 1)[1])
        except ValueError:
            await cb.answer("Invalid value.")
            return
        mute_threshold = group.get("mute_threshold", 3)
        if value >= mute_threshold:
            await cb.answer("Warn threshold must be less than mute threshold.")
            return
        db.set_group_thresholds(group_id, value, mute_threshold)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_update",
            reason="warn_threshold",
            metadata={
                "field": "warn_threshold",
                "old": group.get("warn_threshold"),
                "new": value,
            },
        )
    elif action.startswith("mute:"):
        try:
            value = int(action.split(":", 1)[1])
        except ValueError:
            await cb.answer("Invalid value.")
            return
        warn_threshold = group.get("warn_threshold", 2)
        if value <= warn_threshold:
            await cb.answer("Mute threshold must be greater than warn threshold.")
            return
        db.set_group_thresholds(group_id, warn_threshold, value)
        db.record_audit_event(
            chat_id=group_id,
            actor_user_id=cb.from_user.id,
            action="group_setting_update",
            reason="mute_threshold",
            metadata={
                "field": "mute_threshold",
                "old": group.get("mute_threshold"),
                "new": value,
            },
        )
    elif action.startswith("rag:window:"):
        if not await _require_group_rag_entitlement_cb(cb, db, group_id):
            return
        window_key = action.split(":", 2)[2]
        if window_key not in RAG_WINDOWS:
            await cb.answer("Unknown window.")
            return
        data = await state.get_data()
        filter_key = data.get("rag_filter", "incidents")
        await state.update_data(rag_window=window_key)
        await _edit_message(
            cb.message,
            "Ask a question about this group's moderation history.",
            reply_markup=kb_group_rag_menu(window_key, filter_key),
        )
        await cb.answer("Updated window.")
        return
    elif action.startswith("rag:filter:"):
        if not await _require_group_rag_entitlement_cb(cb, db, group_id):
            return
        filter_key = action.split(":", 2)[2]
        if filter_key not in RAG_ACTION_FILTERS:
            await cb.answer("Unknown filter.")
            return
        data = await state.get_data()
        window_key = data.get("rag_window", "24h")
        await state.update_data(rag_filter=filter_key)
        await _edit_message(
            cb.message,
            "Ask a question about this group's moderation history.",
            reply_markup=kb_group_rag_menu(window_key, filter_key),
        )
        await cb.answer("Updated filter.")
        return
    elif action == "rag:ask":
        if not await _require_group_rag_entitlement_cb(cb, db, group_id):
            return
        data = await state.get_data()
        window_key = data.get("rag_window", "24h")
        filter_key = data.get("rag_filter", "incidents")
        await state.update_data(
            rag_group_id=group_id,
            rag_window=window_key,
            rag_filter=filter_key,
        )
        await state.set_state(Flow.waiting_for_group_rag)
        await _edit_message(
            cb.message,
            "Send your moderation history question as a message.",
            reply_markup=kb_group_rag_menu(window_key, filter_key),
        )
        await cb.answer()
        return
    elif action.startswith("rag:details:"):
        if not await _require_group_rag_entitlement_cb(cb, db, group_id):
            return
        event_id = action.split(":", 2)[2]
        event = db.get_audit_event(group_id, event_id)
        if not event:
            await cb.answer("Audit record not found.")
            return
        data = await state.get_data()
        window_key = data.get("rag_window", "24h")
        filter_key = data.get("rag_filter", "incidents")
        await _edit_message(
            cb.message,
            build_audit_detail(event),
            reply_markup=kb_group_rag_menu(window_key, filter_key),
        )
        await cb.answer()
        return
    elif action.startswith("buy:"):
        plan_id = action.split(":", 1)[1]
        if plan_id in GROUP_PLANS:
            plan = GROUP_PLANS.get(plan_id)
            if not plan:
                await cb.answer("Unknown plan.")
                return
            if not _should_allow_xtr_amount(plan.id, plan.stars, plan.stars, "XTR"):
                await cb.answer("Pricing misconfigured. Please contact admin.", show_alert=True)
                return
            plan_key = build_group_plan_key(plan_id, group_id)
            payload = _create_invoice_record(
                db=db,
                user_id=cb.from_user.id,
                plan_id=plan_key,
                amount=plan.stars,
            )
            if not payload:
                await cb.answer("Failed to create invoice. Please try again.")
                return
            logger.info(
                "Invoice created: plan_id=%s stars_amount=%s payload_len=%s",
                plan_key,
                plan.stars,
                len(payload),
            )
            duration_label = (
                "one-time, non-refundable, lifetime access"
                if plan.duration_days is None
                else f"{plan.duration_days} days"
            )
            prices = [LabeledPrice(label=f"Group {plan.name} ({duration_label})", amount=plan.stars)]
            try:
                await bot.send_invoice(
                    chat_id=cb.from_user.id,
                    title=f"Group {plan.name} Plan",
                    description=(
                        "Per-group subscription. "
                        f"Duration: {duration_label}. "
                        "Activates paid group moderation."
                    ),
                    payload=payload,
                    provider_token="",
                    currency="XTR",
                    prices=prices,
                    start_parameter="resolver_group_sub",
                    need_email=False,
                    need_name=False,
                    need_phone_number=False,
                    need_shipping_address=False,
                    is_flexible=False,
                    disable_notification=True,
                    protect_content=False,
                )
                await cb.answer("I sent you the Stars invoice in your DM.")
                db.record_audit_event(
                    chat_id=group_id,
                    actor_user_id=cb.from_user.id,
                    action="subscription_invoice_created",
                    reason=plan_id,
                    metadata={"plan_id": plan_id, "stars": plan.stars},
                )
            except Exception as exc:
                logger.error("Failed to send group invoice: %s", exc)
                await cb.answer("Failed to create invoice. Please try again.")
                return
        elif plan_id in RAG_ADDON_PLANS:
            if not require_group_entitlement(db, group_id):
                await cb.answer(_subscription_required_notice(), show_alert=True)
                return
            plan = RAG_ADDON_PLANS.get(plan_id)
            if not plan:
                await cb.answer("Unknown plan.")
                return
            if not _should_allow_xtr_amount(plan.id, plan.stars, plan.stars, "XTR"):
                await cb.answer("Pricing misconfigured. Please contact admin.", show_alert=True)
                return
            plan_key = build_rag_plan_key(plan_id, group_id)
            payload = _create_invoice_record(
                db=db,
                user_id=cb.from_user.id,
                plan_id=plan_key,
                amount=plan.stars,
            )
            if not payload:
                await cb.answer("Failed to create invoice. Please try again.")
                return
            logger.info(
                "Invoice created: plan_id=%s stars_amount=%s payload_len=%s",
                plan_key,
                plan.stars,
                len(payload),
            )
            duration_label = (
                f"{plan.duration_days} days" if plan.duration_days is not None else "lifetime"
            )
            prices = [LabeledPrice(label=f"{plan.name} ({duration_label})", amount=plan.stars)]
            try:
                await bot.send_invoice(
                    chat_id=cb.from_user.id,
                    title=plan.name,
                    description=(
                        "Per-group add-on. "
                        f"Duration: {duration_label}. "
                        "Requires an active group subscription."
                    ),
                    payload=payload,
                    provider_token="",
                    currency="XTR",
                    prices=prices,
                    start_parameter="resolver_group_rag",
                    need_email=False,
                    need_name=False,
                    need_phone_number=False,
                    need_shipping_address=False,
                    is_flexible=False,
                    disable_notification=True,
                    protect_content=False,
                )
                await cb.answer("I sent you the Stars invoice in your DM.")
                db.record_audit_event(
                    chat_id=group_id,
                    actor_user_id=cb.from_user.id,
                    action="rag_addon_invoice_created",
                    reason=plan_id,
                    metadata={"plan_id": plan_id, "stars": plan.stars},
                )
            except Exception as exc:
                logger.error("Failed to send RAG add-on invoice: %s", exc)
                await cb.answer("Failed to create invoice. Please try again.")
                return
        else:
            await cb.answer("Unknown plan.")
            return
    else:
        await cb.answer("Unknown action.")
        return

    group = db.get_group_settings(group_id)
    subscription_info = db.get_group_subscription_info(group_id)
    rag_subscription_info = db.get_group_rag_subscription_info(group_id)
    await _edit_message(
        cb.message,
        render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
    )
    await cb.answer("Saved.")


@router.message(Flow.waiting_for_welcome_message)
async def on_group_welcome_message(msg: Message, state: FSMContext, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This action can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return
    if not msg.text:
        await msg.answer("Please send the welcome message as text.")
        return
    if not await _require_group_entitlement_msg(msg, db):
        await state.clear()
        return

    text = msg.text.strip()
    if not text:
        await msg.answer("Please send the welcome message as text.")
        return
    if len(text) > WELCOME_MAX_LENGTH:
        await msg.answer(f"Welcome message is too long (max {WELCOME_MAX_LENGTH} characters).")
        return

    db.set_group_welcome_text(msg.chat.id, text)
    db.record_audit_event(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
        action="group_setting_update",
        reason="welcome_text",
        metadata={"length": len(text)},
    )
    await msg.answer("✅ Welcome message saved.")
    group = db.get_group_settings(msg.chat.id)
    subscription_info = db.get_group_subscription_info(msg.chat.id)
    rag_subscription_info = db.get_group_rag_subscription_info(msg.chat.id)
    await msg.answer(
        render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
    )
    await state.clear()


@router.message(Flow.waiting_for_rules_text)
async def on_group_rules_message(msg: Message, state: FSMContext, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This action can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return
    if not msg.text:
        await msg.answer("Please send the rules text as text.")
        return
    if not await _require_group_entitlement_msg(msg, db):
        await state.clear()
        return

    text = msg.text.strip()
    if not text:
        await msg.answer("Please send the rules text as text.")
        return
    if len(text) > RULES_MAX_LENGTH:
        await msg.answer(f"Rules text is too long (max {RULES_MAX_LENGTH} characters).")
        return

    db.set_group_rules_text(msg.chat.id, text)
    db.record_audit_event(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
        action="group_setting_update",
        reason="rules_text",
        metadata={"length": len(text)},
    )
    await msg.answer("✅ Rules text saved.")
    group = db.get_group_settings(msg.chat.id)
    subscription_info = db.get_group_subscription_info(msg.chat.id)
    rag_subscription_info = db.get_group_rag_subscription_info(msg.chat.id)
    await msg.answer(
        render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
    )
    await state.clear()


@router.message(Flow.waiting_for_security_value)
async def on_group_security_value(msg: Message, state: FSMContext, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This action can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return
    if not msg.text:
        await msg.answer("Please send a numeric value.")
        return
    if not await _require_group_entitlement_msg(msg, db):
        await state.clear()
        return

    data = await state.get_data()
    field = data.get("security_field")
    if field not in {"mute_seconds", "max_warnings"}:
        await msg.answer("Unknown security setting.")
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer("Please send a whole number.")
        return

    if field == "mute_seconds":
        min_val, max_val = SECURITY_MUTE_RANGE
        if value < min_val or value > max_val:
            await msg.answer(f"Mute seconds must be between {min_val} and {max_val}.")
            return
    else:
        min_val, max_val = SECURITY_WARNING_RANGE
        if value < min_val or value > max_val:
            await msg.answer(f"Max warnings must be between {min_val} and {max_val}.")
            return

    group = db.get_group_settings(msg.chat.id)
    config = _parse_security_config(group.get("security_config_json"))
    config[field] = value
    db.set_group_security_config(msg.chat.id, json.dumps(config))
    db.record_audit_event(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
        action="group_setting_update",
        reason="security_config",
        metadata={"field": field, "new": value},
    )
    await msg.answer("✅ Security settings updated.")
    group = db.get_group_settings(msg.chat.id)
    subscription_info = db.get_group_subscription_info(msg.chat.id)
    rag_subscription_info = db.get_group_rag_subscription_info(msg.chat.id)
    await msg.answer(
        render_groupadmin_text(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
        reply_markup=kb_groupadmin(group, subscription_info, rag_subscription_info, settings.feature_v2_groups),
    )
    await state.clear()


@router.message(Flow.waiting_for_group_rag)
async def on_group_rag_query(msg: Message, state: FSMContext, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This query can only be used in groups.")
        return
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return
    if not msg.text:
        await msg.answer("Please send your question as text.")
        return
    if not await _require_group_rag_entitlement_msg(msg, db):
        await state.clear()
        return

    data = await state.get_data()
    window_key = data.get("rag_window", "24h")
    filter_key = data.get("rag_filter", "incidents")
    query = msg.text.strip()
    if not query:
        await msg.answer("Please send your question as text.")
        return

    try:
        db.record_audit_event(
            chat_id=msg.chat.id,
            actor_user_id=msg.from_user.id,
            action="rag_query",
            reason="admin_query",
            metadata={"query": query[:200], "window": window_key, "filter": filter_key},
        )
        events = await retrieve_audit_events(
            db=db,
            chat_id=msg.chat.id,
            query=query,
            window_key=window_key,
            action_filter_key=filter_key,
            top_k=5,
        )
        answer = await build_rag_answer(query, events)
        markup = None
        if events:
            b = InlineKeyboardBuilder()
            for event in events:
                short_id = str(event["event_id"]).split("-")[0]
                b.button(text=f"Details {short_id}", callback_data=f"ga:rag:details:{event['event_id']}")
            b.button(text=f"{EMOJIS['back']} Back", callback_data="ga:menu:rag")
            b.adjust(2)
            markup = b.as_markup()
        if events:
            db.record_audit_event(
                chat_id=msg.chat.id,
                actor_user_id=msg.from_user.id,
                action="rag_answer",
                reason="admin_query",
                metadata={"result_ids": [event["event_id"] for event in events]},
            )
        await msg.answer(answer, reply_markup=markup)
    except Exception:
        logger.error("RAG query failed:\n%s", traceback.format_exc())
        await msg.answer(ERROR_MESSAGES["generic"])
    finally:
        await state.clear()


@router.callback_query(F.data == "retry:menu")
async def retry_menu_handler(cb: CallbackQuery):
    await cb.message.answer("Adjust the tone:", reply_markup=kb_retry_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("retry:"))
async def retry_apply_handler(cb: CallbackQuery, db: DB):
    user_id = cb.from_user.id
    modifier = cb.data.split(":", 1)[1]

    if modifier == "menu":
        await cb.answer()
        return

    user = db.get_user(user_id)
    flags = db.get_retry_flags(user_id)

    goal = user.get("current_goal", "").strip()
    last_text = user.get("last_input_text", "").strip()

    if not goal or not last_text:
        await cb.message.answer("Send /resolve to start again.", reply_markup=kb_goals())
        await cb.answer()
        return

    if flags.get("last_resolve_was_paid") and flags.get("free_retry_available"):
        db.set_retry_flags(user_id, last_paid=True, free_retry=False)

        typing_msg = await cb.message.answer("🔄 Adjusting...")
        responses = await get_llm_client().generate_responses(
            goal,
            last_text,
            modifier,
            language=user.get("language", "en"),
            language_mode=user.get("language_mode", "clean"),
        )
        db.log_interaction(user_id, goal, last_text, responses, used_paid=False)

        await typing_msg.delete()
        await cb.message.answer(render_options(*responses), reply_markup=kb_after_result())
        await cb.answer()
        return

    if user.get("resolves_remaining", 0) <= 0:
        await cb.message.answer(ERROR_MESSAGES["no_resolves"], reply_markup=kb_pricing())
        await cb.answer()
        return

    if db.consume_paid_resolve(user_id):
        db.set_retry_flags(user_id, last_paid=True, free_retry=False)

        typing_msg = await cb.message.answer("🔄 Adjusting...")
        responses = await get_llm_client().generate_responses(
            goal,
            last_text,
            modifier,
            language=user.get("language", "en"),
            language_mode=user.get("language_mode", "clean"),
        )
        db.log_interaction(user_id, goal, last_text, responses, used_paid=True)

        await typing_msg.delete()
        await cb.message.answer(render_options(*responses), reply_markup=kb_after_result())
    else:
        await cb.message.answer(ERROR_MESSAGES["no_resolves"], reply_markup=kb_pricing())

    await cb.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_handler(cb: CallbackQuery, bot: Bot, db: DB):
    plan_id = cb.data.split(":", 1)[1]
    plan = PERSONAL_PLANS.get(plan_id)

    if not plan:
        await cb.answer("Invalid purchase option.")
        return
    if not _should_allow_xtr_amount(plan.id, plan.stars, plan.stars, "XTR"):
        await cb.answer("Pricing misconfigured. Please contact admin.", show_alert=True)
        return

    payload = _create_invoice_record(
        db=db,
        user_id=cb.from_user.id,
        plan_id=build_personal_plan_key(plan.id),
        amount=plan.stars,
    )
    if not payload:
        await cb.answer("Failed to create invoice. Please try again.")
        return

    logger.info(
        "Invoice created: plan_id=%s stars_amount=%s payload_len=%s",
        plan.id,
        plan.stars,
        len(payload),
    )
    prices = [LabeledPrice(label=f"{plan.resolves} Resolves (Personal)", amount=plan.stars)]

    try:
        await bot.send_invoice(
            chat_id=cb.from_user.id,
            title=f"Personal {plan.name} - The Resolver",
            description=f"Personal (DM) plan: {plan.resolves} resolve(s).",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="resolver_bot",
            need_email=False,
            need_name=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False,
            disable_notification=True,
            protect_content=False,
        )
        await cb.answer()
    except Exception as exc:
        logger.error("Failed to send invoice: %s", exc)
        await cb.answer("Failed to create invoice. Please try again.")


@router.message()
async def group_moderation_handler(msg: Message, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        return
    if not msg.text or not msg.from_user:
        return
    if msg.text.startswith("/"):
        return
    if msg.from_user.is_bot:
        return
    if not settings.feature_v2_groups:
        return

    group_id = msg.chat.id
    group = db.get_group_settings(group_id)
    if not group.get("enabled"):
        return
    if not require_group_entitlement(db, group_id):
        await _maybe_notify_group_entitlement(bot, group_id)
        return

    if await is_group_admin(bot, group_id, msg.from_user.id):
        return

    ts = int(msg.date.timestamp()) if msg.date else int(time.time())
    trigger = detect_trigger(msg.text)
    flood_trigger = False
    if group.get("security_enabled"):
        flood_trigger = detect_flood(group_id, msg.from_user.id, ts)
        if flood_trigger:
            trigger = "flood"

    if not trigger:
        return

    language = group.get("language", "en")
    language_mode = group.get("language_mode", "clean")

    if settings.use_llm:
        responses = await get_llm_client().generate_responses(
            "stabilize",
            msg.text,
            language=language,
            language_mode=language_mode,
        )
        deescalation = responses[0]
    else:
        deescalation = (
            GROUP_TEMPLATES["deescalate_flood"]
            if flood_trigger
            else GROUP_TEMPLATES["deescalate"]
        )

    await msg.answer(deescalation)

    violations = db.increment_violations(group_id, msg.from_user.id, ts)
    warn_threshold = int(group.get("warn_threshold", 2))
    mute_threshold = int(group.get("mute_threshold", 3))

    action_taken = "deescalate"
    if violations == warn_threshold:
        await msg.answer(GROUP_TEMPLATES["warn"])
        action_taken = "warn"

    if violations >= mute_threshold:
        if not await _bot_can_restrict(bot, group_id):
            await msg.answer(GROUP_TEMPLATES["permission"])
            action_taken = "mute_failed_permissions"
        else:
            try:
                until_date = datetime.utcnow() + timedelta(minutes=10)
                await bot.restrict_chat_member(
                    chat_id=group_id,
                    user_id=msg.from_user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date,
                )
                await msg.answer(GROUP_TEMPLATES["mute"])
                action_taken = "mute"
                await msg.answer(GROUP_TEMPLATES["notify_admins"])
            except Exception as exc:
                logger.error("Failed to mute user %s in group %s: %s", msg.from_user.id, group_id, exc)
                await msg.answer(GROUP_TEMPLATES["permission"])
                action_taken = "mute_failed_permissions"

    meta_json = json.dumps(
        {
            "violations": violations,
            "warn_threshold": warn_threshold,
            "mute_threshold": mute_threshold,
            "language": language,
            "language_mode": language_mode,
            "flood": flood_trigger,
            "ai_summary": deescalation[:500],
        }
    )
    db.record_moderation_log(
        group_id=group_id,
        user_id=msg.from_user.id,
        trigger=trigger,
        decision_summary=f"violations={violations}",
        action=action_taken,
        meta_json=meta_json,
    )
    db.record_audit_event(
        chat_id=group_id,
        actor_user_id=bot.id,
        target_user_id=msg.from_user.id,
        action=action_taken,
        reason=trigger,
        metadata=json.loads(meta_json),
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, db: DB):
    try:
        payload = pre_checkout_query.invoice_payload
        invoice = db.get_invoice(payload)
        if not invoice:
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        if invoice["status"] != "created":
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        if int(invoice["user_id"]) != pre_checkout_query.from_user.id:
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        now = int(time.time())
        if now - int(invoice["created_at"]) > INVOICE_TTL_SECONDS:
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        if pre_checkout_query.currency != invoice["currency"]:
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        total_amount = _amount_from_total(
            pre_checkout_query.total_amount, pre_checkout_query.currency
        )
        if total_amount != int(invoice["amount"]):
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        group_info = parse_group_plan_key(str(invoice["plan_id"]))
        if group_info:
            plan = GROUP_PLANS.get(group_info["plan_id"])
            if not plan or plan.stars != int(invoice["amount"]):
                await _pre_checkout_fail(
                    pre_checkout_query, "Invoice expired or invalid. Please try again."
                )
                return
            db.ensure_group(int(group_info["group_id"]))
            await pre_checkout_query.answer(ok=True)
            logger.info(
                "Pre-checkout validation: ok=%s payload=%s",
                True,
                payload,
            )
            return

        rag_info = parse_rag_plan_key(str(invoice["plan_id"]))
        if rag_info:
            plan = RAG_ADDON_PLANS.get(rag_info["plan_id"])
            if not plan or plan.stars != int(invoice["amount"]):
                await _pre_checkout_fail(
                    pre_checkout_query, "Invoice expired or invalid. Please try again."
                )
                return
            if not db.group_subscription_active(int(rag_info["group_id"])):
                await _pre_checkout_fail(
                    pre_checkout_query, "Group subscription required for RAG."
                )
                return
            db.ensure_group(int(rag_info["group_id"]))
            await pre_checkout_query.answer(ok=True)
            logger.info(
                "Pre-checkout validation: ok=%s payload=%s",
                True,
                payload,
            )
            return

        personal_plan_id = parse_personal_plan_key(str(invoice["plan_id"])) or str(invoice["plan_id"])
        plan = PERSONAL_PLANS.get(personal_plan_id)
        if not plan or plan.stars != int(invoice["amount"]):
            await _pre_checkout_fail(
                pre_checkout_query, "Invoice expired or invalid. Please try again."
            )
            return

        db.ensure_user(int(invoice["user_id"]))
        await pre_checkout_query.answer(ok=True)
        logger.info(
            "Pre-checkout validation: ok=%s payload=%s",
            True,
            payload,
        )
    except Exception as exc:
        logger.error("Pre-checkout error: %s", exc)
        await pre_checkout_query.answer(ok=False, error_message="Payment validation failed")


@router.message(F.successful_payment)
async def successful_payment(msg: Message, db: DB):
    payment = msg.successful_payment

    try:
        invoice_id = payment.invoice_payload
        invoice = db.get_invoice(invoice_id)
        if not invoice:
            logger.warning("Payment received for unknown invoice %s", invoice_id)
            await msg.answer("Payment verification failed. Please contact support.")
            return

        if int(invoice["user_id"]) != msg.from_user.id:
            await msg.answer("Payment verification failed. Please contact support.")
            return

        group_info = parse_group_plan_key(str(invoice["plan_id"]))
        rag_info = parse_rag_plan_key(str(invoice["plan_id"]))
        if invoice["status"] != "created":
            if group_info:
                await msg.answer("Payment already processed! Your group subscription is active.")
                return
            if rag_info:
                await msg.answer("Payment already processed! Your RAG add-on is active.")
                return
            await msg.answer("Payment already processed! Your resolves are available.")
            return

        now = int(time.time())
        if now - int(invoice["created_at"]) > INVOICE_TTL_SECONDS:
            await msg.answer("Payment verification failed. Please contact support.")
            return

        stars_paid = _amount_from_total(payment.total_amount, payment.currency)
        if stars_paid != int(invoice["amount"]):
            await msg.answer("Payment verification failed. Please contact support.")
            return

        if group_info:
            plan = GROUP_PLANS.get(group_info["plan_id"])
            if not plan or plan.stars != stars_paid:
                logger.error("Group plan mismatch in payment")
                await msg.answer("Payment processing error. Please contact support.")
                return

            transaction_id = payment.telegram_payment_charge_id
            start_ts = int(time.time())
            end_ts = (
                start_ts + plan.duration_days * 86400 if plan.duration_days is not None else None
            )
            status = db.process_group_invoice_payment(
                invoice_id=invoice_id,
                telegram_charge_id=transaction_id,
                group_id=int(group_info["group_id"]),
                plan_id=plan.id,
                stars_amount=plan.stars,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            if status == "duplicate":
                await msg.answer("Payment already processed! Your group subscription is active.")
                return
            if status != "processed":
                await msg.answer("Payment processing error. Please contact support.")
                return

            await msg.answer(
                f"✅ Group subscription activated: {plan.name}.\n"
                f"Group ID: {group_info['group_id']}\n"
                f"Expires: {_format_expiry(end_ts, plan.id)}"
            )
            charge_id_prefix = transaction_id[-6:] if transaction_id else "unknown"
            logger.info(
                "Group payment processed: gid=%s, uid=%s, plan=%s, charge_id_suffix=%s",
                group_info["group_id"],
                msg.from_user.id,
                plan.id,
                charge_id_prefix,
            )
            return

        if rag_info:
            plan = RAG_ADDON_PLANS.get(rag_info["plan_id"])
            if not plan or plan.stars != stars_paid:
                logger.error("RAG add-on mismatch in payment")
                await msg.answer("Payment processing error. Please contact support.")
                return
            if not db.group_subscription_active(int(rag_info["group_id"])):
                await msg.answer("Payment processing error. Please contact support.")
                return

            transaction_id = payment.telegram_payment_charge_id
            start_ts = int(time.time())
            end_ts = (
                start_ts + plan.duration_days * 86400 if plan.duration_days is not None else None
            )
            status = db.process_rag_invoice_payment(
                invoice_id=invoice_id,
                telegram_charge_id=transaction_id,
                group_id=int(rag_info["group_id"]),
                plan_id=plan.id,
                stars_amount=plan.stars,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            if status == "duplicate":
                await msg.answer("Payment already processed! Your RAG add-on is active.")
                return
            if status != "processed":
                await msg.answer("Payment processing error. Please contact support.")
                return

            await msg.answer(
                "✅ RAG add-on activated.\n"
                f"Group ID: {rag_info['group_id']}\n"
                f"Expires: {_format_rag_expiry(end_ts)}"
            )
            charge_id_prefix = transaction_id[-6:] if transaction_id else "unknown"
            logger.info(
                "RAG add-on payment processed: gid=%s, uid=%s, plan=%s, charge_id_suffix=%s",
                rag_info["group_id"],
                msg.from_user.id,
                plan.id,
                charge_id_prefix,
            )
            return

        personal_plan_id = parse_personal_plan_key(str(invoice["plan_id"])) or str(invoice["plan_id"])
        plan = PERSONAL_PLANS.get(personal_plan_id)
        if not plan:
            logger.error("Unknown plan in payment")
            await msg.answer("Payment processing error. Please contact support.")
            return

        transaction_id = payment.telegram_payment_charge_id
        status = db.process_invoice_payment(
            invoice_id=invoice_id,
            telegram_charge_id=transaction_id,
            user_id=msg.from_user.id,
            stars_amount=plan.stars,
            resolves_added=plan.resolves,
        )

        if status == "duplicate":
            await msg.answer("Payment already processed! Your resolves are available.")
            return
        if status != "processed":
            await msg.answer("Payment processing error. Please contact support.")
            return

        user = db.get_user(msg.from_user.id)

        await msg.answer(
            f"✅ Payment successful! Added {plan.resolves} resolves to your account.\n\n"
            f"You now have {user.get('resolves_remaining', 0)} resolves remaining.",
            reply_markup=kb_goals(),
        )

        charge_id_prefix = transaction_id[-6:] if transaction_id else "unknown"
        logger.info(
            "Payment processed: user=%s, plan=%s, charge_id_prefix=%s",
            msg.from_user.id,
            plan.id,
            charge_id_prefix,
        )
        logger.info(
            "Payment credit granted: charge_id=%s resolves_added=%s",
            transaction_id,
            plan.resolves,
        )
    except Exception as exc:
        logger.error("Payment processing error: %s", exc)
        await msg.answer(
            "Payment processing failed. Please contact support with your transaction ID."
        )


@router.message()
async def unknown_message(msg: Message):
    if msg.chat.type != "private":
        return
    await msg.answer(render_unknown_commands(), reply_markup=kb_goals())


@router.callback_query()
async def unknown_callback(cb: CallbackQuery):
    logger.warning("Unhandled callback: %s", cb.data)
    await cb.answer("Unknown action.")
