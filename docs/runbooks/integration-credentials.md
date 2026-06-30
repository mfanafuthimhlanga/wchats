# Runbook: Provisioning Integration Credentials (Phase 16)

**Audience:** Platform operators / deploy engineers  
**Phase:** 16 — Integration Adapters: Platform Credential Service  
**Scope:** Deploy-time setup only. Self-serve credential management UI is Phase 18.

---

## Overview

`integration_credentials` rows store Fernet-encrypted provider API keys in the tenant's
Neon DB. The provisioning script writes these rows at deploy time using the **same
per-tenant HKDF derivation** the API runtime uses to decrypt them, ensuring a consistent
encrypt/decrypt round-trip.

**Single-currency rule (INT-07):** A tenant has exactly one `currency_code` across all
integration providers. The provisioning script enforces this at write time and aborts if
a conflicting currency is already present.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.11+ | With `cryptography`, `psycopg2-binary` installed |
| `PLATFORM_CREDENTIAL_KEY` env var | URL-safe base64-encoded 32-byte master key — source from the Veridian secrets manager |
| `TENANT_DB_CONN_STR` env var or `--conn-str` flag | The tenant's Neon DB connection string (non-pooled, direct) |
| Tenant DB migrations up to 0007 | `integration_credentials` table must exist (alembic_tenant migration 0007) |
| Provider credential file | JSON file with mode 600, containing the raw API key/token |

---

## Running the Provisioning Script

```bash
cd apps/api

# Export the master key from your secrets manager
export PLATFORM_CREDENTIAL_KEY="<URL-safe-base64-encoded-32-byte-key>"

# Export the tenant DB DSN
export TENANT_DB_CONN_STR="postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname"

# Always use --credential-file, never --credential-json on the command line
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type <stripe|shopify|woocommerce|calendly> \
    --credential-file /secure/credential.json \
    --currency-code <ISO-4217-code> \
    --enabled-skills <comma-separated-skills>
```

**Dry run (validate without writing):**
```bash
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type stripe \
    --credential-file /secure/stripe_cred.json \
    --currency-code usd \
    --enabled-skills issue_refund,update_subscription \
    --dry-run
```

---

## Provider-Specific Instructions

### Stripe

#### Creating a Restricted API Key (T-16-03 — scope to enabled skills only)

A Stripe **Restricted API Key** (not a Secret Key) MUST be used. Scope the key to
the minimum permissions required by the enabled skills for this tenant:

| Skill | Required Stripe Permission |
|---|---|
| `issue_refund` | `Refunds → Write` |
| `update_subscription` | `Subscriptions → Write` |
| `place_order` | `Checkout Sessions → Write` |

**Steps to create a Restricted Key:**
1. Log in to the Stripe Dashboard → Developers → API keys.
2. Click **Create restricted key**.
3. Name the key: `veridian-tenant-<tenant-id>` (use the tenant UUID).
4. Enable ONLY the permissions matching the `--enabled-skills` list above.
5. Click **Create key** and copy the key value (it starts with `rk_live_...` or `rk_test_...`).

**Important:** An over-scoped key (e.g. a full Secret Key `sk_live_...`) violates T-16-03.
The gate test (Task 3, Phase 16) optionally verifies that an out-of-scope key fails closed.

**Credential file format** (chmod 600):
```json
{"api_key": "rk_live_..."}
```

**Provision command:**
```bash
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type stripe \
    --credential-file /secure/stripe_cred.json \
    --currency-code usd \
    --enabled-skills issue_refund,update_subscription,place_order
```

---

### Shopify

**Credential file format** (chmod 600):
```json
{"access_token": "shpat_..."}
```

**Config JSON** (required — specify shop URL and API version):
```json
{"shop_url": "https://myshop.myshopify.com", "api_version": "2025-04"}
```

**Provision command:**
```bash
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type shopify \
    --credential-file /secure/shopify_cred.json \
    --currency-code usd \
    --enabled-skills place_order,cancel_order,issue_refund \
    --config-json '{"shop_url": "https://myshop.myshopify.com", "api_version": "2025-04"}'
```

**Notes:**
- Obtain the `access_token` via Shopify's private app / custom app flow in the Partner Dashboard.
- Pin `api_version` to an active Shopify release (check Shopify changelog for EOL dates).
- Always use HTTPS for the shop URL.

---

### WooCommerce

**Credential file format** (chmod 600):
```json
{"consumer_key": "ck_...", "consumer_secret": "cs_..."}
```

**Config JSON** (required — specify site URL):
```json
{"site_url": "https://mystore.example.com"}
```

**Provision command:**
```bash
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type woocommerce \
    --credential-file /secure/woo_cred.json \
    --currency-code gbp \
    --enabled-skills place_order,cancel_order,issue_refund \
    --config-json '{"site_url": "https://mystore.example.com"}'
```

**Notes:**
- Generate the consumer key/secret in WooCommerce → Settings → Advanced → REST API → Add key.
- Always use HTTPS for `site_url`; HTTP requires OAuth1 signing which is more complex.
- The WooCommerce API endpoint is derived from `site_url` by the adapter (`{site_url}/wp-json/wc/v3`).

---

### Calendly

**Calendly paid plan requirement (Pitfall 7):**
The Calendly Scheduling API (`POST /invitees` for programmatic booking) requires a
**paid Calendly plan**. Free plans return 403 Forbidden even with a valid PAT.

If programmatic booking is unavailable, the adapter falls back to returning the
`GET /event_types/{uuid}/scheduling_url` URL, which can be presented to the customer
as a booking link. Document this fallback in the agent's system prompt.

**Credential file format** (chmod 600):
```json
{"personal_access_token": "eyJ..."}
```

**Config JSON** (required — specify event type UUIDs):
```json
{"event_types": ["uuid-of-event-type-1", "uuid-of-event-type-2"]}
```

**Provision command:**
```bash
python scripts/provision_integration_credential.py \
    --tenant-id "<tenant-uuid>" \
    --provider-type calendly \
    --credential-file /secure/calendly_cred.json \
    --currency-code usd \
    --enabled-skills book_slot \
    --config-json '{"event_types": ["evt-uuid-1"]}'
```

**Notes:**
- Obtain the PAT from Calendly → Integrations & apps → API & Webhooks → Personal access tokens.
- `event_types` is a list of Calendly event type UUIDs (found in the Calendly URL when editing the event).

---

## INT-07: Single Currency Enforcement

**A tenant has exactly one currency.** The provisioning script queries all existing
`integration_credentials` rows before inserting. If any row has a different
`currency_code`, the script aborts with exit code 1 and prints:

```
ERROR: INT-07 single-currency guard: this tenant DB already has rows with
currency_code=['gbp']. Cannot add a credential with currency_code='usd'.
```

**If you need to change a tenant's currency:**
1. Remove all existing `integration_credentials` rows from the tenant DB.
2. Re-provision all provider credentials with the new `currency_code`.
3. Update the M8 pre-deploy checklist to confirm the new currency before go-live.

**M8 pre-deploy checklist item (mandatory):**
> Confirm `currency_code` for this tenant with the business owner before provisioning.
> Once set, changing the currency requires re-provisioning all credentials.

---

## Security Checklist

| Check | Command / Verify |
|---|---|
| Script prints no key material | `python scripts/provision_integration_credential.py ... 2>&1 \| grep -E "(rk_|sk_|shpat_|ck_|cs_|eyJ)"` — must return empty |
| Credential file has mode 600 | `stat /secure/credential.json` — mode must be 0600 |
| Stripe key is Restricted (not Secret) | Key starts with `rk_`, not `sk_` |
| Master key not printed | The script only prints row ID, provider_type, currency_code, enabled_skills, config_data |
| No new API endpoints | This provisioning is ONLY via script — no admin API for credential management (Phase 18) |

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `PLATFORM_CREDENTIAL_KEY env var is not set` | Key not exported | `export PLATFORM_CREDENTIAL_KEY=...` from secrets manager |
| `INT-07 single-currency guard` | Conflicting currency in tenant DB | Remove existing rows and re-provision with correct currency |
| `cannot connect to tenant DB` | Wrong DSN or network issue | Verify `TENANT_DB_CONN_STR` and Neon firewall rules |
| `relation "integration_credentials" does not exist` | Migration 0007 not applied | Run `alembic -c alembic_tenant.ini upgrade head` against the tenant DB |
| `skills X are not supported by provider Y` | Wrong skill for provider | See provider → skill mapping table above |
| Stripe 403 with Restricted Key | Key does not have the required permission | Re-create the Restricted Key with the correct permission scopes |
| Calendly 403 on `book_slot` | Free plan limitation | Upgrade to a paid Calendly plan, or use the `scheduling_url` fallback |

---

## Self-Serve Credential Admin UI

Phase 16 delivers **deploy-time provisioning only** (this script + runbook).
A self-serve credential and capability admin UI is planned for **Phase 18**.
Do NOT build admin API endpoints for credential management in Phase 16 or earlier.

---

## Related ADRs and References

- `docs/adr/0002-agent-tool-and-provisioning-strategy.md` — provisioning strategy decision
- `apps/api/app/services/transactional/credential_service.py` — runtime HKDF key derivation
- `apps/api/alembic_tenant/versions/0007_integration_credentials.py` — table schema
- `.planning/phases/16-integration-adapters-platform-credential-service-l5-extensio/16-RESEARCH.md` — Pitfall 7 (Calendly paid plan), INT-07 single-currency, Stripe Restricted Key decision
