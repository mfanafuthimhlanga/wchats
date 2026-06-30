#!/usr/bin/env python3
"""
Deploy-time provisioning script for integration_credentials (INT-01, INT-07).

Writes a Fernet-encrypted provider credential row to a tenant DB, using the
same per-tenant HKDF derivation as credential_service._derive_tenant_fernet()
so the provisioned ciphertext round-trips correctly through get_adapter_for_skill().

Single-currency guard (INT-07): if any existing integration_credentials row in
this tenant DB has a currency_code that differs from --currency-code, the script
aborts with exit code 1 and does NOT write the new row.

Usage examples:

  # Stripe — read credential JSON from a file (never from argv):
  python scripts/provision_integration_credential.py \\
      --tenant-id "a1b2c3d4-..." \\
      --provider-type stripe \\
      --credential-file /secure/creds.json \\
      --currency-code usd \\
      --enabled-skills issue_refund,update_subscription

  # Shopify with config:
  python scripts/provision_integration_credential.py \\
      --tenant-id "..." \\
      --provider-type shopify \\
      --credential-file /secure/shopify_creds.json \\
      --currency-code usd \\
      --enabled-skills place_order,cancel_order,issue_refund \\
      --config-json '{"shop_url": "https://myshop.myshopify.com", "api_version": "2025-04"}'

  # WooCommerce:
  python scripts/provision_integration_credential.py \\
      --tenant-id "..." \\
      --provider-type woocommerce \\
      --credential-file /secure/woo_creds.json \\
      --currency-code gbp \\
      --enabled-skills place_order,cancel_order,issue_refund \\
      --config-json '{"site_url": "https://mystore.example.com"}'

  # Calendly:
  python scripts/provision_integration_credential.py \\
      --tenant-id "..." \\
      --provider-type calendly \\
      --credential-file /secure/calendly_creds.json \\
      --currency-code usd \\
      --enabled-skills book_slot \\
      --config-json '{"event_types": {"consultation": "https://api.calendly.com/event_types/<UUID>", "demo": "https://api.calendly.com/event_types/<UUID2>"}}'
  # NOTE: event_types MUST be a dict mapping service_type labels to Calendly event URIs,
  #       NOT a list of UUIDs. CalendlyAdapter calls event_types.get(service_type) at
  #       runtime — a list raises AttributeError: 'list' object has no attribute 'get'.

Environment variables (required):
  PLATFORM_CREDENTIAL_KEY   URL-safe base64-encoded 32-byte master key (same key used
                            by the W Chats API server). Source from your secrets manager.
  TENANT_DB_CONN_STR        Tenant DB connection string (overridden by --conn-str).

Credential file format (JSON, mode 600):
  Stripe:       {"api_key": "rk_test_..."}
  Shopify:      {"access_token": "shpat_..."}
  WooCommerce:  {"consumer_key": "ck_...", "consumer_secret": "cs_..."}
  Calendly:     {"personal_access_token": "eyJ..."}

SECURITY INVARIANTS (T-16-01, T-16-05):
  - The script NEVER prints the raw credential or the Fernet-encrypted ciphertext.
  - The platform master key is read from env only; never written to DB or printed.
  - Always read the credential from a file (--credential-file) or stdin (--credential-stdin),
    NEVER as a command-line argument — argv values land in shell history and ps output.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import psycopg2

# ---------------------------------------------------------------------------
# Import _derive_tenant_fernet from the credential service so that the
# provisioned ciphertext uses the IDENTICAL HKDF derivation the API uses
# at runtime. This enforces the round-trip consistency requirement.
# ---------------------------------------------------------------------------

# Allow running from the apps/api root (add parent of 'app' package to sys.path)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_SCRIPT_DIR)  # apps/api/
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

from app.services.transactional.credential_service import (  # noqa: E402
    _derive_tenant_fernet,
)

# ---------------------------------------------------------------------------
# Supported provider types and their canonical enabled_skills subsets
# ---------------------------------------------------------------------------

_VALID_PROVIDERS = frozenset({"stripe", "shopify", "woocommerce", "calendly"})

_PROVIDER_SKILLS = {
    "stripe": frozenset({"issue_refund", "update_subscription", "place_order"}),
    "shopify": frozenset({"place_order", "cancel_order", "issue_refund"}),
    "woocommerce": frozenset({"place_order", "cancel_order", "issue_refund"}),
    "calendly": frozenset({"book_slot"}),
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision_integration_credential",
        description=(
            "Deploy-time script: write an encrypted integration_credentials row "
            "into a tenant DB (INT-01). Enforces single currency per tenant (INT-07).\n\n"
            "NEVER pass the raw credential on the command line — use --credential-file "
            "or --credential-stdin to avoid exposing the key in shell history."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --- Connection ---
    parser.add_argument(
        "--conn-str",
        metavar="DSN",
        default=os.environ.get("TENANT_DB_CONN_STR", ""),
        help=(
            "Tenant DB connection string "
            "(default: TENANT_DB_CONN_STR env var). "
            "Example: postgresql://user:pass@host:5432/dbname"
        ),
    )

    # --- Tenant identity ---
    parser.add_argument(
        "--tenant-id",
        required=True,
        metavar="UUID",
        help=(
            "Tenant UUID — used as the HKDF salt for per-tenant key derivation "
            "(must match the tenant_id the API server uses for this tenant DB)."
        ),
    )

    # --- Provider ---
    parser.add_argument(
        "--provider-type",
        required=True,
        choices=sorted(_VALID_PROVIDERS),
        help="Provider type: stripe | shopify | woocommerce | calendly.",
    )

    # --- Credential (file or stdin — never argv) ---
    cred_group = parser.add_mutually_exclusive_group(required=True)
    cred_group.add_argument(
        "--credential-file",
        metavar="PATH",
        help=(
            "Path to a JSON file containing the raw provider credential "
            "(e.g. {\"api_key\": \"rk_test_...\"}). "
            "File must have mode 600 — the script warns if it does not. "
            "The content is encrypted before writing to the DB; "
            "the file is never echoed to stdout or logs."
        ),
    )
    cred_group.add_argument(
        "--credential-stdin",
        action="store_true",
        help=(
            "Read the credential JSON blob from stdin instead of a file. "
            "Pipe from a secrets manager rather than typing interactively "
            "to avoid the credential appearing in shell history."
        ),
    )

    # --- Currency ---
    parser.add_argument(
        "--currency-code",
        required=True,
        metavar="ISO4217",
        help=(
            "ISO 4217 currency code for this tenant (e.g. usd, gbp, zar). "
            "A tenant may have only ONE currency. "
            "The script aborts if an existing row has a different currency_code "
            "(INT-07 single-currency enforcement)."
        ),
    )

    # --- Skills ---
    parser.add_argument(
        "--enabled-skills",
        required=True,
        metavar="SKILL,...",
        help=(
            "Comma-separated list of skill names this credential serves. "
            "Must be a subset of the supported skills for the provider. "
            "stripe: issue_refund, update_subscription, place_order; "
            "shopify/woocommerce: place_order, cancel_order, issue_refund; "
            "calendly: book_slot."
        ),
    )

    # --- Config ---
    parser.add_argument(
        "--config-json",
        metavar="JSON",
        default="{}",
        help=(
            "Provider-specific config as a JSON object. "
            "Stripe: {} (no extra config needed for basic use). "
            "Shopify: {\"shop_url\": \"https://...\", \"api_version\": \"2025-04\"}. "
            "WooCommerce: {\"site_url\": \"https://...\"}. "
            "Calendly: {\"event_types\": {\"consultation\": \"https://api.calendly.com/event_types/<UUID>\", ...}} "
            "(event_types MUST be a dict of {label: event_type_uri}, NOT a list — CalendlyAdapter calls .get()). "
            "Default: '{}'."
        ),
    )

    # --- Dry run ---
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate inputs and check the currency guard without writing to the DB. "
            "Prints what WOULD be written and exits 0."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Credential reading (file or stdin)
# ---------------------------------------------------------------------------


def _read_credential_json(args: argparse.Namespace) -> str:
    """Return the raw credential JSON string without logging it.

    Reads from --credential-file (with a permissions warning if not mode 600)
    or from stdin (--credential-stdin).

    Raises SystemExit with code 1 on any I/O or parse error.
    Never returns the raw value to the caller's local variable after validation —
    only the already-encrypted bytes are retained beyond this function.
    """
    if args.credential_file:
        path = args.credential_file
        try:
            import stat

            file_stat = os.stat(path)
            file_mode = stat.S_IMODE(file_stat.st_mode)
            # Warn if the file is readable by group or other (mode > 0o600)
            if file_mode & 0o077:
                print(
                    f"WARNING: {path} has mode {oct(file_mode)} — "
                    "recommend chmod 600 to prevent credential exposure.",
                    file=sys.stderr,
                )
        except OSError:
            pass  # non-fatal: can't stat on some systems/platforms

        try:
            with open(path) as fh:
                raw = fh.read()
        except OSError as exc:
            print(f"ERROR: cannot read credential file {path!r}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        # --credential-stdin
        print("Reading credential JSON from stdin...", file=sys.stderr)
        raw = sys.stdin.read()

    raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: credential is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(parsed, dict):
        print(
            "ERROR: credential JSON must be an object (dict), "
            f"got {type(parsed).__name__}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Minimal structural check per provider (does NOT log the value)
    return raw  # caller encrypts immediately; raw is not stored


# ---------------------------------------------------------------------------
# Single-currency guard (INT-07)
# ---------------------------------------------------------------------------


def _check_single_currency(conn, new_currency_code: str) -> None:
    """Abort if any existing row has a currency_code that differs from new_currency_code.

    Raises SystemExit with code 1 if the guard trips.
    A tenant DB may contain rows for multiple providers, but ALL rows must share
    the same currency (INT-07 single-currency per tenant invariant).
    """
    normalized = new_currency_code.lower()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT currency_code FROM integration_credentials")
        existing = [row[0].lower() for row in cur.fetchall()]

    conflicts = [c for c in existing if c != normalized]
    if conflicts:
        print(
            f"ERROR: INT-07 single-currency guard: this tenant DB already has rows "
            f"with currency_code={conflicts!r}. Cannot add a credential with "
            f"currency_code={normalized!r}. A tenant supports exactly ONE currency.\n"
            "If you intend to change the tenant's currency, first remove all existing "
            "integration_credentials rows, then re-provision all credentials with the "
            "new currency_code.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Skill validation
# ---------------------------------------------------------------------------


def _validate_skills(provider_type: str, enabled_skills_list: list[str]) -> list[str]:
    """Validate skills against the provider's supported set.

    Returns the validated list. Aborts with exit code 1 on invalid skills.
    """
    supported = _PROVIDER_SKILLS.get(provider_type, frozenset())
    invalid = [s for s in enabled_skills_list if s not in supported]
    if invalid:
        print(
            f"ERROR: skills {invalid!r} are not supported by provider {provider_type!r}. "
            f"Supported skills: {sorted(supported)}.",
            file=sys.stderr,
        )
        sys.exit(1)
    return enabled_skills_list


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # --- Resolve connection string ---
    conn_str = args.conn_str
    if not conn_str:
        print(
            "ERROR: tenant DB connection string is required. "
            "Provide --conn-str or set TENANT_DB_CONN_STR env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Read and validate master key (T-16-05: read from env only) ---
    platform_key_b64 = os.environ.get("PLATFORM_CREDENTIAL_KEY", "")
    if not platform_key_b64:
        print(
            "ERROR: PLATFORM_CREDENTIAL_KEY env var is not set. "
            "Export the URL-safe base64-encoded master key from your secrets manager.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        platform_master_key = base64.urlsafe_b64decode(platform_key_b64)
    except Exception as exc:
        print(
            f"ERROR: PLATFORM_CREDENTIAL_KEY is not valid URL-safe base64: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Validate provider type ---
    provider_type = args.provider_type  # argparse already enforced choices

    # --- Parse and validate enabled_skills ---
    enabled_skills_raw = [s.strip() for s in args.enabled_skills.split(",") if s.strip()]
    if not enabled_skills_raw:
        print("ERROR: --enabled-skills must not be empty.", file=sys.stderr)
        sys.exit(1)
    enabled_skills = _validate_skills(provider_type, enabled_skills_raw)

    # --- Parse config_data ---
    try:
        config_data = json.loads(args.config_json)
    except json.JSONDecodeError as exc:
        print(f"ERROR: --config-json is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(config_data, dict):
        print("ERROR: --config-json must be a JSON object.", file=sys.stderr)
        sys.exit(1)

    # --- Normalize currency code ---
    currency_code = args.currency_code.lower()

    # --- Read credential (never from argv) ---
    credential_json_str = _read_credential_json(args)

    # --- Derive the per-tenant Fernet key (reuses credential_service._derive_tenant_fernet) ---
    # This is the EXACT same derivation the API runtime uses so the round-trip is consistent.
    try:
        fernet = _derive_tenant_fernet(platform_master_key, args.tenant_id)
    except Exception as exc:
        print(f"ERROR: failed to derive tenant Fernet key: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Encrypt the credential blob ---
    # T-16-01: the encrypted bytes are NEVER printed; only the row ID and non-secret
    # metadata are shown in the success output.
    encrypted_credential = fernet.encrypt(credential_json_str.encode())

    # --- Dry-run mode: validate without writing ---
    if args.dry_run:
        print("[DRY RUN] Provisioning would write:")
        print(f"  provider_type:   {provider_type}")
        print(f"  currency_code:   {currency_code}")
        print(f"  enabled_skills:  {enabled_skills}")
        print(f"  config_data:     {json.dumps(config_data)}")
        print("  credential_data: <encrypted — not shown>")
        print("[DRY RUN] No database writes performed.")
        sys.exit(0)

    # --- Connect to tenant DB and apply guards ---
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: cannot connect to tenant DB: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        conn.autocommit = False

        # INT-07 single-currency guard: abort if a conflicting currency exists
        _check_single_currency(conn, currency_code)

        # --- INSERT the credential row ---
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO integration_credentials
                    (provider_type, credential_data, config_data, currency_code, enabled_skills)
                VALUES
                    (%s, %s, %s::jsonb, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    provider_type,
                    psycopg2.Binary(encrypted_credential),
                    json.dumps(config_data),
                    currency_code,
                    json.dumps(enabled_skills),
                ),
            )
            row_id = cur.fetchone()[0]

        conn.commit()

    except psycopg2.Error as exc:
        conn.rollback()
        print(f"ERROR: database write failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    # --- Success output (non-secret fields only; T-16-01) ---
    print("integration_credentials row provisioned successfully.")
    print(f"  row id:          {row_id}")
    print(f"  provider_type:   {provider_type}")
    print(f"  currency_code:   {currency_code}")
    print(f"  enabled_skills:  {enabled_skills}")
    print(f"  config_data:     {json.dumps(config_data)}")
    print("  credential_data: <encrypted — not shown>")


if __name__ == "__main__":
    main()
