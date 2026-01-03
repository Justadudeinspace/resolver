# The Resolver Bot

A Telegram bot that helps you say the right thing without escalating conversations.

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
- Credits are applied only after `successful_payment` validation.

## Commands
- `/start` - Start the bot
- `/resolve` - Start a new resolution
- `/pricing` - View pricing plans
- `/account` - Check your account status
- `/help` - Get help
- `/feedback <message>` - Send feedback

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

## License
Proprietary - All rights reserved.
