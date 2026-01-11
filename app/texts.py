import html
from typing import Dict

EMOJIS = {
    "stabilize": "🧘",
    "clarify": "🧭",
    "close": "🚪",
    "paid": "⭐",
    "free": "🎁",
    "retry": "🔄",
    "stats": "📊",
    "help": "🆘",
    "back": "↩️",
    "buy": "🛒",
    "account": "👤",
    "settings": "⚙️",
    "feedback": "✉️",
}

START_TEXT = (
    f"{EMOJIS['help']} <b>The Resolver</b>\n\n"
    "Say the right thing without escalating.\n\n"
    "<i>Choose a goal, paste a message or describe the situation, "
    "and get clear response options.</i>"
)

GOAL_DESCRIPTIONS = {
    "stabilize": {
        "name": "Stabilize",
        "emoji": EMOJIS["stabilize"],
        "description": "Calm, grounded responses to reduce tension and find common ground.",
    },
    "clarify": {
        "name": "Clarify",
        "emoji": EMOJIS["clarify"],
        "description": "Firm, respectful responses that set clear boundaries and expectations.",
    },
    "close": {
        "name": "Close",
        "emoji": EMOJIS["close"],
        "description": "Direct, composed responses to end a conversation cleanly and respectfully.",
    },
}

GOAL_PROMPTS: Dict[str, str] = {
    key: (
        f"{desc['emoji']} <b>{desc['name']}</b>\n\n"
        f"{desc['description']}\n\n"
        "<i>Paste the message or describe the situation:</i>"
    )
    for key, desc in GOAL_DESCRIPTIONS.items()
}

PRICING_TEXT = (
    f"{EMOJIS['buy']} <b>Pricing</b>\n\n"
    "<b>Personal (DM)</b>\n"
    "🎁 1 Stabilize per day (free)\n"
    "⭐ <b>5 Stars</b> → 1 Resolve\n"
    "⭐ <b>20 Stars</b> → 5 Resolves\n"
    "⭐ <b>50 Stars</b> → 15 Resolves\n\n"
    "<i>No subscriptions or lifetime for Personal.</i>\n\n"
    "<b>Group (PLUS, per-group)</b>\n"
    "⭐ Monthly — 20 Stars\n"
    "⭐ Yearly — 100 Stars\n"
    "⭐ Lifetime — 1000 Stars (permanent)\n"
    "<i>All group features are paid. Admins purchase via /groupadmin.</i>\n\n"
    "<i>Pay with Telegram Stars. Tap a plan to open a Stars invoice.</i>\n"
    "<i>Resolves are added only after successful payment.</i>"
)

HELP_TEXT = (
    f"{EMOJIS['help']} <b>Help</b>\n\n"
    "<b>Resolve flow:</b>\n"
    "1) /resolve or choose a goal\n"
    "2) Paste the message or describe the situation\n"
    "3) Pick one of the response options\n\n"
    "<b>Commands:</b>\n"
    "/start — main menu\n"
    "/resolve — choose a goal and resolve\n"
    "/pricing — view Personal and Group pricing\n"
    "/buy — purchase a plan\n"
    "/account — usage and resolves\n"
    "/settings — defaults + language (v2 personal)\n"
    "/groupadmin — group settings (admins only)\n"
    "/grouplogs — moderation logs (admins only)\n"
    "Use the group admin panel for welcome/rules/security settings and audit/RAG queries.\n"
    "/help — this screen\n"
    "/feedback — send feedback\n\n"
    "<b>Payments:</b>\n"
    "Plans use Telegram Stars (XTR). Resolves are added after successful payment."
)

ACCOUNT_TEMPLATE = (
    f"{EMOJIS['account']} <b>Your Account</b>\n\n"
    "⭐ <b>Paid resolves remaining:</b> {paid_resolves}\n"
    "🎁 <b>Free stabilize today:</b> {free_status}\n"
    "📊 <b>Total uses:</b> {total_uses}\n"
    "🗓️ <b>Account age:</b> {account_age} days"
)

ERROR_MESSAGES = {
    "no_resolves": (
        f"{EMOJIS['buy']} <b>Out of Resolves</b>\n\n"
        "You've used all available resolves.\n"
        "Open /pricing to grab a Stars bundle."
    ),
    "invalid_input": (
        "✍️ <b>Input too long</b>\n\n"
        "Please keep your message under {max_length} characters.\n"
        "Try summarizing the situation more concisely."
    ),
    "rate_limit": (
        "⏰ <b>Too Many Requests</b>\n\n"
        "Please wait a moment before trying again."
    ),
    "config_missing": (
        "⚠️ <b>Configuration missing</b>\n\n"
        "Please set BOT_TOKEN in .env."
    ),
    "generic": (
        "⚠️ <b>Something went wrong</b>\n\n"
        "Please try again in a moment."
    ),
}

BOT_COMMANDS = [
    ("start", "Begin. Choose a goal and resolve a message."),
    ("resolve", "Resolve a message by choosing a goal and getting clear options."),
    ("pricing", "View Personal and Group pricing."),
    ("buy", "Purchase resolve bundles with Stars."),
    ("account", "View your remaining resolves and usage."),
    ("settings", "Set defaults and language (v2 personal)."),
    ("groupadmin", "Admin-only group control panel."),
    ("grouplogs", "Admin-only moderation logs."),
    ("help", "Learn how to use The Resolver."),
    ("feedback", "Send feedback to improve the bot."),
]

SETTINGS_TEXT = (
    f"{EMOJIS['settings']} <b>Settings (defaults)</b>\n"
    "Set what I preselect for you. You can change this anytime.\n\n"
)

SETTINGS_STATUS = "Default goal: {default_goal}\nDefault tone: {default_style}\n"

SETTINGS_V2_DISABLED = (
    "⚠️ <b>V2 Personal is disabled.</b>\n"
    "Language and language mode controls are unavailable.\n\n"
)

FEEDBACK_PROMPT = (
    f"{EMOJIS['feedback']} <b>Feedback</b>\n\n"
    "Share what felt good or what should improve. Send one message and I'll log it."
)

FEEDBACK_THANKS = "Thank you for your feedback! 🙏"


def render_options(a: str, b: str, c: str) -> str:
    safe_a = html.escape(a, quote=False)
    safe_b = html.escape(b, quote=False)
    safe_c = html.escape(c, quote=False)
    return (
        "🧠 <b>Response Options</b>\n\n"
        f"<b>A.</b> {safe_a}\n\n"
        f"<b>B.</b> {safe_b}\n\n"
        f"<b>C.</b> {safe_c}\n\n"
        "<i>Pick the one that feels right for your situation.</i>"
    )
