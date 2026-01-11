# Resolver Launch Checklist

This checklist covers what Codex fixed, what still requires human action, and how to verify each item.

## A) Repo Integrity (Codex-verifiable)
- [ ] ✅ Codex fixed: README clone command and file structure paths now match the repo.
  - HOW TO VERIFY: `rg -n "clone https://github.com/Justadudeinspace/resolver.git|docs/MVP_CHECKLIST.md" README.md`
- [ ] ✅ Codex fixed: `.env.example` now uses a non-functional placeholder for `INVOICE_SECRET`.
  - HOW TO VERIFY: `rg -n "INVOICE_SECRET=CHANGE_ME" .env.example`
- [ ] ✅ Codex fixed: `run_resolver.sh` now blocks placeholder or too-short `INVOICE_SECRET` values.
  - HOW TO VERIFY: `rg -n "INVOICE_SECRET" run_resolver.sh`
- [ ] ✅ Codex fixed: settings validation flags placeholder `INVOICE_SECRET` values.
  - HOW TO VERIFY: `rg -n "invoice_secret_valid" app/config.py`
- [ ] Compile step passes.
  - HOW TO VERIFY: `python -m compileall app`
- [ ] Smoke check passes.
  - HOW TO VERIFY: `python scripts/smoke_check.py`
- [ ] Database health check succeeds.
  - HOW TO VERIFY: `python -c "from app.config import settings; from app.db import DB; print(DB(settings.db_path).health_check())"`
- [ ] Handlers/router import succeeds.
  - HOW TO VERIFY: `python -c "from app.handlers import router; print(router)"`
- [ ] Runtime directories are auto-created on first run (`data/`, `logs/`).
  - HOW TO VERIFY: `./run_resolver.sh` then `ls -d data logs`

## B) Environment Setup (Operator must do)
- [ ] Set `BOT_TOKEN` in `.env` (from @BotFather).
  - HOW TO VERIFY: `rg -n "^BOT_TOKEN=" .env`
- [ ] Set `INVOICE_SECRET` to a unique 32+ character random string.
  - HOW TO VERIFY: `rg -n "^INVOICE_SECRET=" .env` (confirm it is not a placeholder and length ≥ 32)
- [ ] Configure LLM usage:
  - If using OpenAI: set `USE_LLM=true` and `OPENAI_API_KEY`.
  - If not using OpenAI: set `USE_LLM=false` (bot falls back to templates).
  - HOW TO VERIFY: `rg -n "^USE_LLM=|^OPENAI_API_KEY=" .env`
- [ ] Confirm optional settings as needed (model, temperature, rate limits, input length, DB path, log level).
  - HOW TO VERIFY: `rg -n "^LLM_MODEL=|^LLM_TEMPERATURE=|^RATE_LIMIT_PER_USER=|^MAX_INPUT_LENGTH=|^DB_PATH=|^LOG_LEVEL=" .env`

## C) Telegram-side Setup (Operator must do)
- [ ] Set BotFather `/setcommands` to match the bot exactly:
  - `/start`
  - `/resolve`
  - `/pricing`
  - `/buy`
  - `/account`
  - `/settings`
  - `/help`
  - `/feedback`
  - HOW TO VERIFY: Open @BotFather → `/getcommands` and confirm the list matches.
- [ ] Enable Telegram Stars monetization and confirm Stars settings.
  - Notes: currency must be `XTR` and provider_token must be empty.
  - HOW TO VERIFY: In @BotFather → Payments → Stars enabled; in code: `rg -n "currency=\"XTR\"|provider_token=\"\"" app/handlers.py`
- [ ] Privacy mode settings (if using in groups): disable privacy in @BotFather so the bot receives all group messages.
  - HOW TO VERIFY: @BotFather → Bot Settings → Group Privacy = Disabled.

## D) Payments Verification (Operator must do)
- [ ] Test a Stars purchase end-to-end (pricing → invoice → pre-checkout → successful payment).
  - HOW TO VERIFY: Use `/pricing` or `/buy personal_monthly` in Telegram, complete a Stars purchase.
- [ ] Confirm pre-checkout validation accepts valid payloads and rejects invalid payloads.
  - HOW TO VERIFY: Check `logs/resolver.log` for `Pre-checkout error` (should be absent on success).
- [ ] Confirm successful payment credits resolves exactly once (idempotent).
  - HOW TO VERIFY: `sqlite3 ./data/resolver.sqlite3 "select transaction_id, count(*) from purchases group by transaction_id having count(*) > 1;"` (should return no rows)
- [ ] Confirm balances and purchases are recorded.
  - HOW TO VERIFY: `sqlite3 ./data/resolver.sqlite3 "select resolves_remaining from users where user_id=<YOUR_TELEGRAM_ID>;"`
- [ ] Confirm log lines indicate success.
  - HOW TO VERIFY: `rg -n "Payment processed" logs/resolver.log`

## E) LLM Verification (Operator must do)
- [ ] Verify fallback templates are used when LLM is disabled or key is missing.
  - HOW TO VERIFY: Set `USE_LLM=false`, run `/resolve`, and confirm responses still appear with the fallback notice.
- [ ] Verify OpenAI path works when enabled.
  - HOW TO VERIFY: Set `USE_LLM=true` with `OPENAI_API_KEY`, restart, and check `logs/resolver.log` for `LLM client initialized with OpenAI`.
- [ ] Confirm no local model download is required.
  - HOW TO VERIFY: There is no local model asset; only OpenAI API is used (`rg -n "AsyncOpenAI" app/llm.py`).

## F) Operational Readiness (Optional but recommended)
- [ ] Run in the background (example using nohup).
  - HOW TO VERIFY: `nohup ./run_resolver.sh > logs/nohup.out 2>&1 &` then `ps aux | rg "app.main"`
- [ ] Confirm log location and permissions.
  - HOW TO VERIFY: `ls -l logs/resolver.log`
- [ ] Backup the SQLite database regularly.
  - HOW TO VERIFY: `sqlite3 ./data/resolver.sqlite3 ".backup ./data/resolver.sqlite3.bak"`
- [ ] Update dependencies safely.
  - HOW TO VERIFY: `python -m pip install --upgrade -r requirements.txt` then `python -m compileall app`
