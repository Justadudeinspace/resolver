<p align="center">
  <img src="docs/assets/resolver.png" alt="The Resolver Bot" width="300">
</p>

# The Resolver Bot

A Telegram bot that helps you say the right thing without escalating conversations.

<p align="center">
  <img src="docs/assets/resolver_welcome.png" alt="The Resolver Welcome" width="420">
</p>

## Features
- **Stabilize**: Calm, grounded responses to reduce tension
- **Clarify**: Firm, respectful responses that set clear boundaries
- **Close**: Direct, composed responses to end conversations cleanly
- **Free tier**: 1 Stabilize resolve per day
- **Paid tier**: All goals + paid resolves with one free retry after a paid resolve
- **Payments**: Telegram Stars invoices (XTR) with signed payloads and verification

## Quick Start

### 1) Clone and install
```bash
git clone git@github.com:Justadudeinspace.git
cd resolver
chmod +x install_resolver.sh
./install_resolver.sh
```

### 2) Configure
```bash
cp .env.example .env
# Edit .env with your bot token from @BotFather
```

### 3) Run
```bash
./run_resolver.sh
```

## Termux Install (Android)
```bash
pkg install git
chmod +x install_resolver.sh
./install_resolver.sh
./run_resolver.sh
```

## Environment Variables
- `BOT_TOKEN` - Telegram bot token from @BotFather
- `USE_LLM` - `true` or `false` to enable OpenAI usage
- `OPENAI_API_KEY` - Optional (only if using LLM)
- `LLM_MODEL` - Model name (default: `gpt-4o-mini`)
- `LLM_TEMPERATURE` - Response creativity
- `RATE_LIMIT_PER_USER` - Requests per minute
- `MAX_INPUT_LENGTH` - Max characters in input
- `DB_PATH` - SQLite path (default: `./data/resolver.sqlite3`)
- `INVOICE_SECRET` - 32+ char secret for signing invoice payloads
- `LOG_LEVEL` - Logging level (INFO, DEBUG, etc.)

## Telegram Stars Notes
- Stars invoices require enabling **Stars** monetization in @BotFather.
- The bot uses `currency="XTR"` and an empty `provider_token`.
- Buttons open Telegram Stars invoices, and resolves are added only after `successful_payment` validation.

## Core Flow
1) `/start` → choose a goal
2) Send your text
3) Receive 3 response options
4) Use **Retry** modifiers (Softer / Firmer / Shorter)

## Defaults (Settings)
- Use `/settings` to set a **default goal** and **default tone**.
- If a default goal is set, `/resolve` auto-selects it and prompts for text (tap another goal to switch).
- Default tone is applied to the first response set unless you choose a retry modifier.

## Rules
- **Free tier**: 1 Stabilize resolve per day.
- **Retry rule**: One free retry after a paid resolve; additional retries consume paid resolves.
- **Stars plans**:
  - Starter: 5 ⭐ = 1 resolve
  - Bundle: 20 ⭐ = 5 resolves
  - Pro: 50 ⭐ = 15 resolves

## Commands
- `/start` - Begin. Choose a goal and resolve a message.
- `/resolve` - Resolve a message by choosing a goal and getting clear options.
- `/pricing` - View resolve pricing and Star bundles.
- `/buy` - Purchase resolve bundles with Stars.
- `/account` - View your remaining resolves and usage.
- `/settings` - Set your default goal and response style.
- `/help` - Learn how to use The Resolver.
- `/feedback` - Send feedback to improve the bot.

## BotFather commands
```
/start
/resolve
/pricing
/buy
/account
/settings
/help
/feedback
```

## File Structure
```
.
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── handlers.py
│   ├── llm.py
│   ├── main.py
│   ├── middlewares.py
│   ├── payments.py
│   ├── states.py
│   └── texts.py
├── .env.example
├── .gitignore
├── install_resolver.sh
├── LICENSE
├── MVP_CHECKLIST.md
├── requirements.txt
├── run_resolver.sh
└── README.md
```

## Development
```bash
python -m app.main
python -m compileall app
```

## Changelog
- Added settings defaults for goal/style and applied them to `/resolve` with a Change goal option.
- Added a feedback submenu and database logging for feedback messages.

## License
Proprietary - All rights reserved.
