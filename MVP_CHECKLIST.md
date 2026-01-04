# Resolver MVP Checklist

## First run
- [~] `./install_resolver.sh` completes without errors
- [~] `.env` created from `.env.example`
- [ ] BOT_TOKEN set in `.env`
- [~] INVOICE_SECRET set to a 32+ char random string

## Start-up
- [ ] `./run_resolver.sh` starts without errors
- [ ] `python -m compileall app` passes
- [ ] `python -c "from app.config import settings; from app.db import DB; print(DB(settings.db_path).health_check())"` returns `True`

## Core flow
- [~] /start shows goal selector
- [~] Selecting a goal prompts for input text
- [~] Input returns 3 response options
- [~] Retry modifiers return 3 updated options

## Payments (Telegram Stars)
- [~] /pricing shows Stars plans and buy buttons
- [~] Pre-checkout validation succeeds for valid payloads
- [ ] Resolves are added only after `successful_payment`
- [ ] Duplicate payment IDs do not credit twice
