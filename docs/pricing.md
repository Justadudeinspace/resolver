# Resolver Pricing (Authoritative)

Pricing is locked and enforced in code. The canonical source is `app/pricing.py`, and all invoices must reference those values.

## Personal (DM)
- Personal Monthly: 50 ⭐
- Personal Yearly: 450 ⭐
- Personal Lifetime: 1000 ⭐

## Group (PLUS, per-group)
- Group Monthly: 150 ⭐
- Group Yearly: 1500 ⭐
- Group Charter: 4000 ⭐
- Charter is **one-time, non-refundable, lifetime access**.

## RAG Add-On (per-group)
- RAG Monthly Add-On: 50 ⭐
- Requires an active group subscription and explicit add-on entitlement.

## Enforcement
- All invoices use Telegram Stars (XTR) and the pricing above.
- If a pricing lookup fails, the purchase is denied.
- If entitlement checks fail, the action is denied.
