# V2 Group Admin Smoke Tests

1) Non-admin denied
- In a group, run `/groupadmin` as a non-admin.
- Expect: "This command is restricted to group admins."

2) Admin panel loads
- Run `/groupadmin` as an admin.
- Expect: Admin panel renders with current settings and subscription status.

3) Toggle features persist
- Toggle Resolver enable/disable, welcome, rules, or security.
- Re-open `/groupadmin` and verify settings persist.

4) Set welcome/rules/security settings
- From `/groupadmin`, set a welcome message and rules text.
- Open security settings and toggle anti-link/anti-spam; set mute seconds and max warnings.
- Re-open `/groupadmin` and confirm settings persist.

5) Restart bot → settings persist
- Restart the bot process.
- Re-open `/groupadmin` and verify settings persist.

6) Group mode blocked without subscription
- Enable Resolver in a group without an active subscription.
- Send a triggering message.
- Expect: No moderation action is taken.

7) Group mode works with subscription
- Activate a group subscription.
- Send a triggering message from a non-admin.
- Expect: De-escalation response, warn/mute ladder, and log entry.

8) RAG query works for admins
- From `/groupadmin`, open the RAG query panel and ask a question.
- Expect: Summary response with audit ID citations and “Details” buttons.

9) v1.0 DM features unaffected
- In DM, run `/resolve`, `/pricing`, `/account`, `/settings`.
- Expect: DM flows behave as before.
