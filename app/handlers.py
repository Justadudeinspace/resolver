import logging
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .db import DB
from .llm import llm_client
from .payments import PLANS, create_invoice_payload, verify_and_parse_payload, verify_stars_payment
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


def kb_settings():
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
    b.button(text=f"{EMOJIS['back']} Main menu", callback_data="nav:goals")
    b.adjust(3, 1, 2, 2, 1, 1)
    return b.as_markup()


def render_settings_text(user: dict) -> str:
    default_goal = user.get("default_goal")
    default_style = user.get("default_style")

    goal_label = (
        GOAL_DESCRIPTIONS[default_goal]["name"]
        if default_goal in GOAL_DESCRIPTIONS
        else "None"
    )
    style_label = STYLE_OPTIONS.get(default_style, "None")
    return SETTINGS_TEXT + SETTINGS_STATUS.format(
        default_goal=goal_label, default_style=style_label
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
async def cmd_buy(msg: Message, command: CommandObject, bot: Bot):
    plan_id = (command.args or "").strip().lower()
    if plan_id in PLANS:
        payload = create_invoice_payload(msg.from_user.id, plan_id)
        if not payload:
            await msg.answer("Payments are not configured yet.")
            return

        plan = PLANS[plan_id]
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
    await msg.answer(render_settings_text(user), reply_markup=kb_settings())


@router.message(Command("feedback"))
async def cmd_feedback(msg: Message, command: CommandObject, state: FSMContext, db: DB):
    await state.clear()
    feedback = command.args
    if feedback:
        db.add_feedback(msg.from_user.id, feedback.strip())
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
        await _edit_or_send(cb.message, render_settings_text(user), reply_markup=kb_settings())
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
    else:
        await cb.answer("Unknown setting.")
        return

    user = db.get_user(user_id)
    await _edit_or_send(cb.message, render_settings_text(user), reply_markup=kb_settings())
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

    if user.get("resolves_remaining", 0) > 0 and db.consume_paid_resolve(user_id):
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=True, free_retry=True)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await llm_client.generate_responses(goal, text, modifier)
        db.log_interaction(user_id, goal, text, responses, used_paid=True)

        await typing_msg.delete()
        await msg.answer(render_options(*responses), reply_markup=kb_after_result())
        return

    if goal == "stabilize" and db.can_use_free_today(user_id):
        db.mark_free_used_today(user_id)
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=False, free_retry=False)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await llm_client.generate_responses(goal, text, modifier)
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

    db.add_feedback(msg.from_user.id, feedback)
    logger.info(
        "Feedback received from user %s (length=%s)",
        msg.from_user.id,
        len(feedback),
    )
    logger.debug("Feedback detail from user %s: %s", msg.from_user.id, feedback)
    await state.clear()
    await msg.answer(FEEDBACK_THANKS, reply_markup=kb_goals())


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
        responses = await llm_client.generate_responses(goal, last_text, modifier)
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
        responses = await llm_client.generate_responses(goal, last_text, modifier)
        db.log_interaction(user_id, goal, last_text, responses, used_paid=True)

        await typing_msg.delete()
        await cb.message.answer(render_options(*responses), reply_markup=kb_after_result())
    else:
        await cb.message.answer(ERROR_MESSAGES["no_resolves"], reply_markup=kb_pricing())

    await cb.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_handler(cb: CallbackQuery, bot: Bot):
    plan_id = cb.data.split(":", 1)[1]
    plan = PLANS.get(plan_id)

    if not plan:
        await cb.answer("Invalid purchase option.")
        return

    payload = create_invoice_payload(cb.from_user.id, plan_id)
    if not payload:
        await cb.answer("Payments are not configured yet.")
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


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, db: DB):
    try:
        data = verify_and_parse_payload(pre_checkout_query.invoice_payload)
        if not data:
            await pre_checkout_query.answer(ok=False, error_message="Invalid invoice")
            return

        if int(data["uid"]) != pre_checkout_query.from_user.id:
            await pre_checkout_query.answer(ok=False, error_message="Invalid invoice")
            return

        plan = PLANS.get(data["plan"])
        if not plan:
            await pre_checkout_query.answer(ok=False, error_message="Invalid plan")
            return

        if pre_checkout_query.total_amount // 100 != plan.stars:
            await pre_checkout_query.answer(ok=False, error_message="Invalid payment amount")
            return

        db.ensure_user(data["uid"])
        await pre_checkout_query.answer(ok=True)
    except Exception as exc:
        logger.error("Pre-checkout error: %s", exc)
        await pre_checkout_query.answer(ok=False, error_message="Payment validation failed")


@router.message(F.successful_payment)
async def successful_payment(msg: Message, db: DB):
    payment = msg.successful_payment

    try:
        stars_paid = payment.total_amount // 100
        data = verify_stars_payment(stars_paid, payment.invoice_payload)
        if not data:
            logger.warning("Invalid payment received from user %s", msg.from_user.id)
            await msg.answer("Payment verification failed. Please contact support.")
            return

        if int(data["uid"]) != msg.from_user.id:
            await msg.answer("Payment verification failed. Please contact support.")
            return

        plan = PLANS.get(data["plan"])
        if not plan:
            logger.error("Unknown plan in payment")
            await msg.answer("Payment processing error. Please contact support.")
            return

        transaction_id = payment.telegram_payment_charge_id
        success = db.add_resolves(
            user_id=msg.from_user.id,
            stars_amount=plan.stars,
            resolves_added=plan.resolves,
            transaction_id=transaction_id,
        )

        if not success:
            await msg.answer("Payment already processed! Your resolves are available.")
            return

        user = db.get_user(msg.from_user.id)

        await msg.answer(
            f"✅ Payment successful! Added {plan.resolves} resolves to your account.\n\n"
            f"You now have {user.get('resolves_remaining', 0)} resolves remaining.",
            reply_markup=kb_goals(),
        )

        charge_id_prefix = transaction_id[:6] if transaction_id else "unknown"
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
    await msg.answer(render_unknown_commands(), reply_markup=kb_goals())
