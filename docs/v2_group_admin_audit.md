# V2 Group Admin Audit (Phase 0)

## Group routers / handlers
- `app/handlers.py`:
  - `/groupadmin` command handler (`cmd_groupadmin`).
  - `/grouplogs` command handler (`cmd_grouplogs`).
  - Group admin callbacks handler (`groupadmin_handler`, namespaced `ga:` callbacks).
  - Group moderation flow (`group_moderation_handler`).
  - Payment hooks for groups (`pre_checkout`, `successful_payment`).

## /groupadmin command and callbacks
- `/groupadmin` and `ga:*` callbacks are defined in `app/handlers.py` and render the admin panel via `render_groupadmin_text` and `kb_groupadmin`.
- Admin checks use Telegram `get_chat_member` through the shared helper `is_group_admin`.

## Group settings storage
- `app/db.py`:
  - `groups` table stores settings keyed by `group_id` (chat id).
  - `get_group_settings` loads defaults and ensures a row exists.
  - Toggle/update helpers: `set_group_enabled`, `set_group_language`, `set_group_language_mode`, `set_group_thresholds`, `set_group_toggle`.

## Subscription / entitlement logic
- `app/db.py`:
  - `group_subscriptions` table stores group subscriptions.
  - `group_subscription_active` and `get_group_subscription_info` read entitlement state.
  - `process_group_invoice_payment` stores paid subscriptions.
- `app/handlers.py`:
  - Group entitlement gate uses `require_group_entitlement`.
  - Invoices are generated from `GROUP_PLANS` and validated in `pre_checkout` and `successful_payment`.

## Pricing constants
- `app/payments.py`:
  - `GROUP_MONTHLY_STARS`, `GROUP_YEARLY_STARS`, `GROUP_LIFETIME_STARS`.
  - `GROUP_PLANS` is the canonical source for group plan pricing and durations.

## Feature flags (env / config)
- `app/config.py`:
  - `FEATURE_V2_GROUPS` toggles group moderation.
  - `FEATURE_V2_PERSONAL` toggles v2 personal settings.
