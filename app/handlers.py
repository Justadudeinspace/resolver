import json
import logging
import re
import time
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
from .llm import llm_client
from .payments import (
    GROUP_PLANS,
    PLANS,
    INVOICE_TTL_SECONDS,
    build_group_plan_key,
    generate_invoice_id,
    parse_group_plan_key,
)
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

GROUP_TEMPLATES = {
    "deescalate": "Let’s keep this respectful and calm so everyone feels safe to talk.",
    "deescalate_flood": "Please slow down and give others space to respond.",
    "warn": "⚠️ Please keep the conversation respectful. Continued escalation may lead to a temporary mute.",
    "mute": "🔇 Temporarily muting to cool things down (10 minutes).",
    "permission": "⚠️ I need permission to mute members. Admins, please enable the restriction permission.",
    "notify_admins": "Admins notified: moderation action taken.",
}


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


async def _edit_or_send(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup)


def _fallback_notice() -> str:
    return (
        "\n\n⚠️ <b>AI mode is disabled.</b> "
        "Replies are generated using safe fallback templates. "
        "Set USE_LLM=true and OPENAI_API_KEY in .env to enable AI."
    )


def _maybe_add_fallback(text: str) -> str:
    return text + _fallback_notice() if not settings.use_llm else text


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
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


def _format_expiry(end_ts: Optional[int]) -> str:
    if not end_ts:
        return "Never"
    return datetime.utcfromtimestamp(end_ts).strftime("%Y-%m-%d")


def render_groupadmin_text(group: dict, subscription_info: dict, feature_enabled: bool) -> str:
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
    expires = _format_expiry(subscription_info.get("end_ts"))
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
        f"Subscription: {subscription_status} (expires: {expires})"
    )


def kb_groupadmin(group: dict, subscription_info: dict, feature_enabled: bool):
    b = InlineKeyboardBuilder()
    if not feature_enabled:
        b.button(text=f"{EMOJIS['back']} Close", callback_data="group:menu:close")
        b.adjust(1)
        return b.as_markup()

    enabled = bool(group.get("enabled"))
    b.button(
        text="✅ Enable" if not enabled else "🚫 Disable",
        callback_data="group:toggle_enabled",
    )
    b.button(text="🌐 Language", callback_data="group:menu:language")
    b.button(text="🧭 Mode", callback_data="group:menu:mode")
    b.button(text=f"Warn threshold ({group.get('warn_threshold')})", callback_data="group:menu:warn")
    b.button(text=f"Mute threshold ({group.get('mute_threshold')})", callback_data="group:menu:mute")
    b.button(
        text=f"Welcome {'On' if group.get('welcome_enabled') else 'Off'}",
        callback_data="group:toggle:welcome_enabled",
    )
    b.button(
        text=f"Rules {'On' if group.get('rules_enabled') else 'Off'}",
        callback_data="group:toggle:rules_enabled",
    )
    b.button(
        text=f"Security {'On' if group.get('security_enabled') else 'Off'}",
        callback_data="group:toggle:security_enabled",
    )

    if not subscription_info.get("active"):
        b.button(text="⭐ Buy Monthly", callback_data="group:buy:group_monthly")
        b.button(text="⭐ Buy Yearly", callback_data="group:buy:group_yearly")
        b.button(text="⭐ Buy Lifetime", callback_data="group:buy:group_lifetime")
    b.button(text=f"{EMOJIS['back']} Close", callback_data="group:menu:close")
    b.adjust(2, 2, 2, 2, 2, 1)
    return b.as_markup()


def kb_group_language_menu():
    b = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGUAGES:
        label = LANGUAGE_LABELS.get(code, code)
        b.button(text=f"{label} ({code})", callback_data=f"group:lang:{code}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="group:menu:main")
    b.adjust(2)
    return b.as_markup()


def kb_group_mode_menu():
    b = InlineKeyboardBuilder()
    for mode in LANGUAGE_MODES:
        label = LANGUAGE_MODE_LABELS.get(mode, mode.title())
        b.button(text=f"{label}", callback_data=f"group:mode:{mode}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="group:menu:main")
    b.adjust(1)
    return b.as_markup()


def kb_group_threshold_menu(threshold_type: str):
    b = InlineKeyboardBuilder()
    if threshold_type == "warn":
        options = range(1, 6)
    else:
        options = range(2, 7)
    for value in options:
        b.button(text=str(value), callback_data=f"group:{threshold_type}:{value}")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="group:menu:main")
    b.adjust(3, 3, 1)
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
    b.button(text="⭐ 5 Stars — 1 Resolve", callback_data="buy:starter")
    b.button(text="⭐ 20 Stars — 5 Resolves", callback_data="buy:bundle")
    b.button(text="⭐ 50 Stars — 15 Resolves", callback_data="buy:pro")
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

    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(3, 1, 2, 2, 2, 1)
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
    if plan_id in PLANS:
        plan = PLANS[plan_id]
        payload = _create_invoice_record(
            db=db,
            user_id=msg.from_user.id,
            plan_id=plan.id,
            amount=plan.stars,
        )
        if not payload:
            await msg.answer("Failed to create invoice. Please try again.")
            return

        prices = [LabeledPrice(label=f"{plan.resolves} Resolves", amount=plan.stars * 100)]
        try:
            await bot.send_invoice(
                chat_id=msg.from_user.id,
                title=f"{plan.name} - The Resolver",
                description=f"Get {plan.resolves} resolve(s) for The Resolver bot",
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
    v2_personal_enabled = settings.feature_v2_personal
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
        v2_personal_enabled = settings.feature_v2_personal
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

    v2_personal_enabled = settings.feature_v2_personal

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
            user = db.get_user(user_id)
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
        responses = await llm_client.generate_responses(
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
        responses = await llm_client.generate_responses(
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
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.answer("This command is restricted to group admins.")
        return

    group = db.get_group(msg.chat.id)
    subscription_info = db.get_group_subscription_info(msg.chat.id)
    text = render_groupadmin_text(group, subscription_info, settings.feature_v2_groups)
    await msg.answer(
        text,
        reply_markup=kb_groupadmin(group, subscription_info, settings.feature_v2_groups),
    )


@router.message(Command("grouplogs"))
async def cmd_grouplogs(msg: Message, bot: Bot, db: DB):
    if msg.chat.type not in {"group", "supergroup"}:
        await msg.answer("This command can only be used in groups.")
        return
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
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


@router.callback_query(F.data.startswith("group:"))
async def groupadmin_handler(cb: CallbackQuery, bot: Bot, db: DB):
    if not cb.message:
        await cb.answer()
        return

    if cb.message.chat.type not in {"group", "supergroup"}:
        await cb.answer("Group-only command.")
        return

    if not await _is_admin(bot, cb.message.chat.id, cb.from_user.id):
        await cb.answer("Admins only.")
        return

    group_id = cb.message.chat.id
    if not settings.feature_v2_groups and cb.data not in {"group:menu:close"}:
        await _edit_or_send(
            cb.message,
            render_groupadmin_text({}, {}, False),
            reply_markup=kb_groupadmin({}, {}, False),
        )
        await cb.answer()
        return

    action = cb.data.split(":", 1)[1]

    if action == "menu:close":
        await _edit_or_send(cb.message, "Admin panel closed.")
        await cb.answer()
        return
    if action == "menu:main":
        group = db.get_group(group_id)
        subscription_info = db.get_group_subscription_info(group_id)
        await _edit_or_send(
            cb.message,
            render_groupadmin_text(group, subscription_info, settings.feature_v2_groups),
            reply_markup=kb_groupadmin(group, subscription_info, settings.feature_v2_groups),
        )
        await cb.answer()
        return
    if action == "menu:language":
        await _edit_or_send(cb.message, "Choose group language:", reply_markup=kb_group_language_menu())
        await cb.answer()
        return
    if action == "menu:mode":
        await _edit_or_send(cb.message, "Choose group language mode:", reply_markup=kb_group_mode_menu())
        await cb.answer()
        return
    if action == "menu:warn":
        await _edit_or_send(
            cb.message,
            "Set warning threshold:",
            reply_markup=kb_group_threshold_menu("warn"),
        )
        await cb.answer()
        return
    if action == "menu:mute":
        await _edit_or_send(
            cb.message,
            "Set mute threshold:",
            reply_markup=kb_group_threshold_menu("mute"),
        )
        await cb.answer()
        return
    if action == "toggle_enabled":
        group = db.get_group(group_id)
        db.set_group_enabled(group_id, not bool(group.get("enabled")))
    elif action.startswith("toggle:"):
        field = action.split(":", 1)[1]
        group = db.get_group(group_id)
        current = bool(group.get(field))
        db.set_group_toggle(group_id, field, not current)
    elif action.startswith("lang:"):
        language = action.split(":", 1)[1]
        if language not in SUPPORTED_LANGUAGES:
            await cb.answer("Unknown language.")
            return
        db.set_group_language(group_id, language)
    elif action.startswith("mode:"):
        mode = action.split(":", 1)[1]
        if mode not in LANGUAGE_MODES:
            await cb.answer("Unknown mode.")
            return
        db.set_group_language_mode(group_id, mode)
    elif action.startswith("warn:"):
        try:
            value = int(action.split(":", 1)[1])
        except ValueError:
            await cb.answer("Invalid value.")
            return
        group = db.get_group(group_id)
        mute_threshold = group.get("mute_threshold", 3)
        if value >= mute_threshold:
            await cb.answer("Warn threshold must be less than mute threshold.")
            return
        db.set_group_thresholds(group_id, value, mute_threshold)
    elif action.startswith("mute:"):
        try:
            value = int(action.split(":", 1)[1])
        except ValueError:
            await cb.answer("Invalid value.")
            return
        group = db.get_group(group_id)
        warn_threshold = group.get("warn_threshold", 2)
        if value <= warn_threshold:
            await cb.answer("Mute threshold must be greater than warn threshold.")
            return
        db.set_group_thresholds(group_id, warn_threshold, value)
    elif action.startswith("buy:"):
        plan_id = action.split(":", 1)[1]
        plan = GROUP_PLANS.get(plan_id)
        if not plan:
            await cb.answer("Unknown plan.")
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
        prices = [LabeledPrice(label=f"Group Subscription {plan.name}", amount=plan.stars * 100)]
        try:
            await bot.send_invoice(
                chat_id=cb.from_user.id,
                title=f"Group {plan.name} Subscription",
                description=f"Activate group moderation for {plan.name.lower()} billing.",
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
            await cb.message.answer("I sent you the Stars invoice in your DM.")
        except Exception as exc:
            logger.error("Failed to send group invoice: %s", exc)
            await cb.answer("Failed to create invoice. Please try again.")
            return
    else:
        await cb.answer("Unknown action.")
        return

    group = db.get_group(group_id)
    subscription_info = db.get_group_subscription_info(group_id)
    await _edit_or_send(
        cb.message,
        render_groupadmin_text(group, subscription_info, settings.feature_v2_groups),
        reply_markup=kb_groupadmin(group, subscription_info, settings.feature_v2_groups),
    )
    await cb.answer("Saved.")


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
        responses = await llm_client.generate_responses(
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
        responses = await llm_client.generate_responses(
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
    plan = PLANS.get(plan_id)

    if not plan:
        await cb.answer("Invalid purchase option.")
        return

    payload = _create_invoice_record(
        db=db,
        user_id=cb.from_user.id,
        plan_id=plan.id,
        amount=plan.stars,
    )
    if not payload:
        await cb.answer("Failed to create invoice. Please try again.")
        return

    prices = [LabeledPrice(label=f"{plan.resolves} Resolves", amount=plan.stars * 100)]

    try:
        await bot.send_invoice(
            chat_id=cb.from_user.id,
            title=f"{plan.name} - The Resolver",
            description=f"Get {plan.resolves} resolve(s) for The Resolver bot",
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
    group = db.get_group(group_id)
    if not group.get("enabled"):
        return
    if not db.group_subscription_active(group_id):
        return

    if await _is_admin(bot, group_id, msg.from_user.id):
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
        responses = await llm_client.generate_responses(
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


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, db: DB):
    try:
        payload = pre_checkout_query.invoice_payload
        invoice = db.get_invoice(payload)
        if not invoice:
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        if invoice["status"] != "created":
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        if int(invoice["user_id"]) != pre_checkout_query.from_user.id:
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        now = int(time.time())
        if now - int(invoice["created_at"]) > INVOICE_TTL_SECONDS:
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        if pre_checkout_query.currency != invoice["currency"]:
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        if pre_checkout_query.total_amount // 100 != int(invoice["amount"]):
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        group_info = parse_group_plan_key(str(invoice["plan_id"]))
        if group_info:
            plan = GROUP_PLANS.get(group_info["plan_id"])
            if not plan or plan.stars != int(invoice["amount"]):
                await pre_checkout_query.answer(
                    ok=False, error_message="Invoice expired or invalid. Please try again."
                )
                return
            db.ensure_group(int(group_info["group_id"]))
            await pre_checkout_query.answer(ok=True)
            return

        plan = PLANS.get(str(invoice["plan_id"]))
        if not plan or plan.stars != int(invoice["amount"]):
            await pre_checkout_query.answer(
                ok=False, error_message="Invoice expired or invalid. Please try again."
            )
            return

        db.ensure_user(int(invoice["user_id"]))
        await pre_checkout_query.answer(ok=True)
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
        if invoice["status"] != "created":
            if group_info:
                await msg.answer("Payment already processed! Your group subscription is active.")
                return
            await msg.answer("Payment already processed! Your resolves are available.")
            return

        now = int(time.time())
        if now - int(invoice["created_at"]) > INVOICE_TTL_SECONDS:
            await msg.answer("Payment verification failed. Please contact support.")
            return

        stars_paid = payment.total_amount // 100
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
                f"Expires: {_format_expiry(end_ts)}"
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

        plan = PLANS.get(str(invoice["plan_id"]))
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
