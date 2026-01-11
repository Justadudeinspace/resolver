# Launch Ready Checklist

## Telegram setup
- Create your bot with @BotFather and record the BOT_TOKEN.
- Enable Telegram Stars payments for the bot.
- Make sure your plan IDs match those configured in `app/pricing.py` (personal_monthly, personal_yearly, personal_lifetime; group_monthly, group_yearly, group_charter; rag_monthly).

## Local setup
1) Install dependencies:
   ```bash
   ./install_resolver.sh
   ```
2) Create and edit your environment file:
   ```bash
   cp .env.example .env
   ```
   Required values:
   - `BOT_TOKEN`
   - `INVOICE_SECRET` (32+ random characters)
   - `OPENAI_API_KEY` (only if `USE_LLM=true`)
3) Run the bot:
   ```bash
   ./run_resolver.sh
   ```

## Verification commands
Run from the repo root:
```bash
python -m compileall app
python -c "from app.config import settings; from app.db import DB; print(DB(settings.db_path).health_check())"
```
Optional (if present):
```bash
PYTHONPATH=. python scripts/smoke_check.py
```

## Common failures
- **Missing BOT_TOKEN**: the bot cannot start; set `BOT_TOKEN` in `.env`.
- **Invalid INVOICE_SECRET**: must be 32+ chars and not a placeholder; payments will be rejected.
- **Missing OPENAI_API_KEY**: only required when `USE_LLM=true`; otherwise the bot uses template responses.
