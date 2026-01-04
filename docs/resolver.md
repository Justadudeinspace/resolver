# Resolver Source of Truth

This file documents the current repo structure, the startup commands, and the MVP requirements implemented.

## Starting Files
| File | Purpose |
| --- | --- |
| README.md | Setup, commands, and environment variables |
| install_resolver.sh | Cross-platform installer |
| run_resolver.sh | One-command boot script |
| requirements.txt | Pinned Python dependencies |
| .env.example | Environment variable template |
| .gitignore | Prevents secrets, DBs, logs, venvs, caches from commit |
| LICENSE | Proprietary license |
| MVP_CHECKLIST.md | Ready-to-run checklist |
| app/__init__.py | Package init |
| app/main.py | Bot entrypoint (aiogram v3) |
| app/config.py | Pydantic settings |
| app/db.py | SQLite layer with WAL + busy_timeout |
| app/handlers.py | Bot handlers and flows |
| app/llm.py | OpenAI + fallback response generator |
| app/texts.py | User-facing strings |
| app/states.py | FSM states |
| app/middlewares.py | Rate-limit middleware |
| app/payments.py | Telegram Stars payload signing/verification |
