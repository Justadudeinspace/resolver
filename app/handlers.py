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
    render_options,
)

logger = logging.getLogger(__name__)
router = Router()


def kb_goals():
    b = InlineKeyboardBuilder()

    for goal_key, goal_desc in GOAL_DESCRIPTIONS.items():
        b.button(
            text=f"{goal_desc['emoji']} {goal_desc['name']}",
            callback_data=f"goal:{goal_key}",
        )

    b.button(text=f"{EMOJIS['buy']} Pricing", callback_data="nav:pricing")
    b.button(text=f"{EMOJIS['help']} Help", callback_data="nav:help")
    b.button(text=f"{EMOJIS['account']} Account", callback_data="nav:account")
    b.adjust(3, 1, 1, 1)
    return b.as_markup()


def kb_after_result():
    b = InlineKeyboardBuilder()
    b.button(text=f"{EMOJIS['retry']} Retry", callback_data="retry:menu")
    b.button(text=f"{EMOJIS['buy']} Get more", callback_data="nav:pricing")
    b.button(text=f"{EMOJIS['back']} Back to goals", callback_data="nav:goals")
    b.adjust(2, 1)
    return b.as_markup()


def kb_pricing():
    b = InlineKeyboardBuilder()
    b.button(text="⭐ 5 Stars — 1 Resolve", callback_data="buy:p1")
    b.button(text="⭐ 20 Stars — 5 Resolves", callback_data="buy:p5")
    b.button(text="⭐ 50 Stars — 15 Resolves", callback_data="buy:p15")
    b.button(text=f"{EMOJIS['back']} Back", callback_data="nav:goals")
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


@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext, db: DB):
    await state.clear()

    db.ensure_user(
        user_id=msg.from_user.id,
        username=msg.from_user.username,
        first_name=msg.from_user.first_name,
        last_name=msg.from_user.last_name,
    )

    await msg.answer(START_TEXT, reply_markup=kb_goals())
    logger.info("User %s started the bot", msg.from_user.id)


@router.message(Command("resolve"))
async def cmd_resolve(msg: Message, state: FSMContext, db: DB):
    await state.clear()
    db.ensure_user(msg.from_user.id)
    await msg.answer("Choose a goal:", reply_markup=kb_goals())


@router.message(Command("pricing"))
async def cmd_pricing(msg: Message):
    await msg.answer(PRICING_TEXT, reply_markup=kb_pricing())


@router.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(HELP_TEXT, reply_markup=kb_goals())


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

    await msg.answer(text)


@router.message(Command("feedback"))
async def cmd_feedback(msg: Message, command: CommandObject):
    feedback = command.args
    if feedback:
        logger.info("Feedback from %s: %s", msg.from_user.id, feedback)
        await msg.answer("Thank you for your feedback! 🙏")
    else:
        await msg.answer("Please provide feedback: /feedback <your feedback>")


@router.callback_query(F.data.startswith("nav:"))
async def nav_handler(cb: CallbackQuery, state: FSMContext, db: DB):
    action = cb.data.split(":", 1)[1]

    try:
        if action == "pricing":
            await cb.message.edit_text(PRICING_TEXT, reply_markup=kb_pricing())
        elif action == "help":
            await cb.message.edit_text(HELP_TEXT, reply_markup=kb_goals())
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
            await cb.message.edit_text(text, reply_markup=kb_goals())
        else:
            await state.clear()
            await cb.message.edit_text("Choose a goal:", reply_markup=kb_goals())
    except TelegramBadRequest:
        pass

    await cb.answer()


@router.callback_query(F.data.startswith("goal:"))
async def choose_goal(cb: CallbackQuery, state: FSMContext, db: DB):
    goal = cb.data.split(":", 1)[1]
    user_id = cb.from_user.id

    db.ensure_user(user_id)
    db.set_goal(user_id, goal)
    db.set_retry_flags(user_id, last_paid=False, free_retry=False)

    await state.set_state(Flow.waiting_for_text)
    await cb.message.edit_text(GOAL_PROMPTS[goal])
    await cb.answer()


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

    if user.get("resolves_remaining", 0) > 0 and db.consume_paid_resolve(user_id):
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=True, free_retry=True)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await llm_client.generate_responses(goal, text)
        db.log_interaction(user_id, goal, text, responses, used_paid=True)

        await typing_msg.delete()
        await msg.answer(render_options(*responses), reply_markup=kb_after_result())
        return

    if goal == "stabilize" and db.can_use_free_today(user_id):
        db.mark_free_used_today(user_id)
        db.set_last_input(user_id, text)
        db.set_retry_flags(user_id, last_paid=False, free_retry=False)

        typing_msg = await msg.answer("🧠 Thinking...")
        responses = await llm_client.generate_responses(goal, text)
        db.log_interaction(user_id, goal, text, responses, used_paid=False)

        await typing_msg.delete()
        await msg.answer(render_options(*responses), reply_markup=kb_after_result())
        return

    await msg.answer(ERROR_MESSAGES["no_resolves"], reply_markup=kb_pricing())


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
            logger.error("Unknown plan in payment: %s", data["plan"])
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

        logger.info(
            "Payment processed: user=%s, stars=%s, resolves=%s",
            msg.from_user.id,
            plan.stars,
            plan.resolves,
        )
    except Exception as exc:
        logger.error("Payment processing error: %s", exc)
        await msg.answer(
            "Payment processing failed. Please contact support with your transaction ID."
        )


@router.message()
async def unknown_message(msg: Message):
    await msg.answer(
        "I didn't understand that. Try:\n"
        "/start - Start the bot\n"
        "/resolve - Start a new resolution\n"
        "/help - Get help\n"
        "/account - Check your account"
    )
