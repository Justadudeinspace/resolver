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
    "⭐ <b>5 Stars</b> → 1 Resolve\n"
    "⭐ <b>20 Stars</b> → 5 Resolves\n"
    "⭐ <b>50 Stars</b> → 15 Resolves\n\n"
    "<i>Most people keep a small bundle so they're not stuck mid-conversation.</i>"
)

HELP_TEXT = (
    f"{EMOJIS['help']} <b>Help Guide</b>\n\n"
    "<b>How it works:</b>\n"
    "1) Choose a goal: Stabilize / Clarify / Close\n"
    "2) Paste the message or describe the situation\n"
    "3) Pick one of the response options\n\n"
    "<b>Free tier:</b>\n"
    f"{EMOJIS['free']} 1 Stabilize resolve per day\n\n"
    "<b>Paid tier:</b>\n"
    f"{EMOJIS['paid']} All goals + retries"
)

ACCOUNT_TEMPLATE = (
    f"{EMOJIS['account']} <b>Your Account</b>\n\n"
    "⭐ <b>Paid resolves remaining:</b> {paid_resolves}\n"
    "🎁 <b>Free resolve today:</b> {free_status}\n"
    "📊 <b>Total uses:</b> {total_uses}\n"
    "🗓️ <b>Account age:</b> {account_age} days"
)

ERROR_MESSAGES = {
    "no_resolves": (
        f"{EMOJIS['buy']} <b>Out of Resolves</b>\n\n"
        "You've used all your available resolves.\n"
        "Grab a small bundle so you're not stuck mid-conversation."
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
        "Please set BOT_TOKEN and INVOICE_SECRET in .env."
    ),
    "generic": (
        "⚠️ <b>Something went wrong</b>\n\n"
        "Please try again in a moment."
    ),
}

BOT_COMMANDS = [
    ("start", "Begin. Choose a goal and resolve a message."),
    ("resolve", "Resolve a message by choosing a goal and getting clear options."),
    ("pricing", "View resolve pricing and Star bundles."),
    ("buy", "Purchase resolve bundles with Stars."),
    ("account", "View your remaining resolves and usage."),
    ("settings", "Set your default goal and response style."),
    ("help", "Learn how to use The Resolver."),
    ("feedback", "Send feedback to improve the bot."),
]

SETTINGS_TEMPLATE = (
    f"{EMOJIS['settings']} <b>Settings</b>\n\n"
    "Default goal: {default_goal}\n"
    "Default style: {default_style}\n\n"
    "<i>Pick defaults to speed up /resolve.</i>"
)


def render_options(a: str, b: str, c: str) -> str:
    return (
        "🧠 <b>Response Options</b>\n\n"
        f"<b>A.</b> {a}\n\n"
        f"<b>B.</b> {b}\n\n"
        f"<b>C.</b> {c}\n\n"
        "<i>Pick the one that feels right for your situation.</i>"
    )
