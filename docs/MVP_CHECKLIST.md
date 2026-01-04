# Resolver MVP Checklist

## First run
- [x] `./install_resolver.sh` completes without errors
- [x] `.env` created from `.env.example`
- [x] BOT_TOKEN set in `.env`
- [x] INVOICE_SECRET set to a 32+ char random string

## Start-up
- [x] `./run_resolver.sh` starts without errors
- [x] `python -m compileall app` passes
- [x] `python -c "from app.config import settings; from app.db import DB; print(DB(settings.db_path).health_check())"` returns `True`

## Core flow
- [x] /start shows goal selector
- [x] Selecting a goal prompts for input text
- [x] Input returns 3 response options
- [x] Retry modifiers return 3 updated options

## Payments (Telegram Stars)
- [x] /pricing shows Stars plans and buy buttons
- [x] Pre-checkout validation succeeds for valid payloads
- [x] Resolves are added only after `successful_payment`
- [x] Duplicate payment IDs do not credit twice
