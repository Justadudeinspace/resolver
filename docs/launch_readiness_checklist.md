# Launch Readiness Checklist (v2)

## Configuration
- [ ] Set `BOT_TOKEN` and `INVOICE_SECRET` (32+ chars) in `.env`.
- [ ] Ensure `DB_PATH` points to a writable location.
- [ ] Confirm `FEATURE_V2_PERSONAL` and `FEATURE_V2_GROUPS` are set to `false` until you are ready.
- [ ] Set `USE_LLM=true` and `OPENAI_API_KEY` if you want AI responses.

## Payments
- [ ] Test Telegram Stars invoices in a staging bot.
- [ ] Verify pre-checkout and successful payment callbacks for:
  - [ ] DM resolve credits
- [ ] Group subscriptions (monthly/yearly/charter)
- [ ] Confirm idempotency with duplicate `telegram_payment_charge_id`.

## Database
- [ ] Run `python -m compileall app`.
- [ ] Run `python -c "from app.config import settings; from app.db import DB; print(DB(settings.db_path).health_check())"`.
- [ ] Back up existing `resolver.sqlite3` before upgrading.

## Group Moderation
- [ ] Add the bot to a test group and grant admin permissions:
  - Restrict members (mute)
  - Delete messages (not required)
- [ ] Enable the group with `/groupadmin`.
- [ ] Purchase a group subscription and confirm status changes to Active.
- [ ] Validate moderation ladder (de-escalate → warn → temp mute).
- [ ] Confirm `/grouplogs` shows the last 20 actions.

## V2 Personal
- [ ] Enable `FEATURE_V2_PERSONAL=true`.
- [ ] Use `/settings` to set language and language mode.
- [ ] Confirm AI responses are in the selected language.

## Deployment
- [ ] Ensure `run_resolver.sh` is executable.
- [ ] Run `./run_resolver.sh` to start the bot.
- [ ] Monitor `logs/resolver.log` for errors.
