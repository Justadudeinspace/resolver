# V2 Group Admin (PLUS)

## Overview
Resolver v2 group moderation is **opt-in** and **admin-only**. Group functionality is gated by a paid subscription and is disabled by default.

## Enable V2 group mode
1. Set `FEATURE_V2_GROUPS=true` in your environment.
2. Add the bot to a group and make it an admin.
3. Run `/groupadmin` in the group to open the admin panel.

## Admin-only access
- `/groupadmin` and `/grouplogs` are restricted to Telegram group admins.
- Admin status is verified on every callback using `get_chat_member`.

## Group admin panel controls
The panel uses message editing and always reflects the current DB state.

Controls:
- Enable / Disable Resolver
- RAG query panel (admin-only)
- Set language
- Set language mode (Clean / Adult / Unrestricted)
- Configure escalation thresholds (warn / mute)
- Toggle welcome messages
- Toggle rules message
- Toggle security features (anti-flood)
- View subscription status
- Back / Close panel

## Default group settings
Defaults are stored per `chat_id`:
- `resolver_enabled = false`
- `language = "en"`
- `language_mode = "clean"`
- `warn_threshold = 2`
- `mute_threshold = 3`
- `welcome_enabled = false`
- `rules_enabled = false`
- `security_enabled = false`

## Subscription gating
Group moderation requires an active subscription:
- If group moderation is enabled but there is no active subscription, the bot **does not moderate**.
- Entitlement checks fail closed on errors.
- Optional admin notifications are rate-limited to avoid spam.

### Pricing (Telegram Stars)
Group subscriptions are billed via Telegram Stars using the canonical pricing in `app/payments.py`:
- Monthly: 20 ⭐ → 30 days
- Yearly: 100 ⭐ → 365 days
- Lifetime: 1000 ⭐ → no expiry

## Moderation ladder (group mode)
Detect → De-escalate → Warn → Temp mute → Notify admins

Rules:
- No auto-bans.
- Admins are never moderated.
- All moderation actions are logged with timestamps, rule, AI summary, and action taken.

## Logs
- `/grouplogs` shows the last 20 moderation actions for admins.
- RAG queries use audit records only (no raw chat history).
- Each RAG response cites audit IDs for traceability and optional detail views.
