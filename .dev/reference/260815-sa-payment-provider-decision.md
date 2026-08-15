# The SA payment provider decision: Paystack, three rails, and why not Stripe or PayFast

The payment design behind MASTERPLAN M6 and rows `7.10`/`7.11`, decided 2026-08-15. Reader: the
session building the Paystack adapter or the POP queue, and anyone re-asking "why not X".

## The constraint that shapes everything

The agent's transactional skills (`issue_refund`, `update_subscription`, `place_order`) are only as
automatable as the provider API behind them. Card rails expose all three operations as API calls.
EFT is a push payment: usable for pay-in, but no SA provider exposes a real refund-by-API for it
at parity with card, and recurring on bank rails is DebiCheck debit orders, a different system
entirely. **The agent is card-shaped because the APIs are card-shaped.**

## Provider comparison (2026 sources, verify at signup)

| Provider | Settlement | Fees approx | Agent-facing API |
|---|---|---|---|
| **Paystack** (chosen) | T+1, payouts free | 2.9% + R1.50 | Refund API by transaction reference (full/partial); subscriptions via `plan_code` on transaction initialize; transaction initialize as checkout link. Channels in SA: card, EFT (Pay with Bank), Capitec Pay, SnapScan |
| PayFast | T+1 | 2.4-3.5% | Pay-in easy (signed form + ITN webhook). **Refunds are dashboard-driven**, wallet-funded, R2 each, 2-3 business days on EFT: nothing an agent can call. Recurring card-only |
| Yoco | ~24h | ~2.6% online | Fits tenants needing a physical card machine; weaker fit here |
| Ozow | instant EFT rails | low | EFT-only, refunds clunky |
| Stripe direct | ~T+7 for Sub-Saharan Africa | 2.9% + 30c | SA merchant availability uncertain across 2026 sources. Stripe owns Paystack; Paystack is its African arm |

Stripe direct was the unexamined default inherited from the PRD. Its two failures for this market:
availability for SA-registered entities is at best in flux, and settlement of ~T+7 is the worst in
the table where the local norm is next business day.

## The three rails (owner decision)

1. **Paystack card**: the agent's automation rail. All three skills API-driven.
2. **Instant EFT as an optional extra, delivered as a Paystack channel** (`channels:
   ["card", "eft"]` on transaction initialize). Never a second provider: PayFast would add a second
   adapter, second credential and split reconciliation to buy a hosted page Paystack channels
   already provide, while its dashboard-only refunds blind `issue_refund` on every order it took.
3. **The static FNB account stays, agent-served, human-approved** (`7.11`): the agent hands out the
   bank details to customers who choose manual EFT; the order waits for an uploaded proof of
   payment; a console queue that only a Clerk-JWT holder can action approves it — the tenant,
   structurally never the agent (the `4.7` machine-credential refusal pattern). Refunds on this
   rail have no API anywhere and route through `pending_confirmations` for the owner to execute.

## Engineering notes for `7.10`

- **TXN-02 idempotency mapping changes.** Stripe's native `Idempotency-Key` header does not exist
  on Paystack: pay-in idempotency rides the unique transaction `reference`; refund dedup rides the
  platform's own `idempotency.py`. Design it, do not port the Stripe assumption.
- The Stripe adapter and `test_stripe_live` stay in the tree, parked for a future international
  tenant. `test_paystack_live` gets authored with the same gating shape.
- POP files (`7.11`) are untrusted customer content: stored via `storage_service`, shown to humans,
  never entering agent context or corpus. The SEC-02 boundary applies fully.

## Verify at Paystack signup, not assumed

- Actual settlement terms for the account (table above is from comparison articles).
- Whether EFT-channel transactions refund through the API at parity with card-paid ones; bank-rail
  refunds are often slower even when the API accepts the request.
