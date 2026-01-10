# Admin Permissions Guide

## Required Telegram Permissions
For group moderation to work, the bot must be an admin in the group with:
- **Restrict members** (required to apply temporary mutes)
- **Delete messages** (not used in v2; optional)

## Admin-only Commands
These commands only work for group admins:
- `/groupadmin` — open the admin control panel
- `/grouplogs` — view the last 20 moderation actions

Non-admins who invoke these commands receive:
`This command is restricted to group admins.`

## Safe Defaults
- Groups are disabled by default.
- Moderation actions run only when:
  1. `FEATURE_V2_GROUPS=true`
  2. `groups.enabled=1`
  3. A valid group subscription is active

## Troubleshooting
If mutes fail, the bot will notify the group that it lacks permissions. Grant
the **Restrict members** permission and retry.
