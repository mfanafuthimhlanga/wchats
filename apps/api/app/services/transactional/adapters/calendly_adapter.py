"""
transactional.adapters.calendly_adapter — Real Calendly provider adapter (INT-06).

Implements ProviderAdapter for the Calendly Scheduling API, covering:
  - book_slot → POST https://api.calendly.com/invitees (Bearer PAT authentication)

Unsupported methods (place_order, cancel_order, issue_refund, update_subscription,
update_customer_record) raise NotImplementedError so the dispatcher returns
is_error=True without a network call.

Calendly does not have an official Python SDK — httpx (already a core dep at 0.28.1)
is used for native async HTTP calls. No asyncio.to_thread needed (httpx is async-native).

Open Question 2 resolution (RESEARCH.md):
  The Calendly Scheduling API requires an event_type URI (not a human-readable service
  label). BookSlotInput.service_type is a human label (e.g. "consultation"). To keep the
  typed schema provider-agnostic, the mapping from service_type → event_type URI is
  stored in integration_credentials.config_data["event_types"]:

      config_data = {
          "event_types": {
              "consultation": "https://api.calendly.com/event_types/<UUID>",
              "demo":         "https://api.calendly.com/event_types/<UUID2>",
          }
      }

  The CalendlyAdapter reads self._event_types from config_data at __init__ and resolves
  args.service_type at book_slot call time. If the service_type is absent from the map,
  a ValueError is raised before any HTTP call (so the dispatcher audits it as a config error).

Security invariants enforced here:
  T-16-01: The personal_access_token is NEVER logged. It is extracted from the credential
            JSON blob only inside book_slot via handle.use() and placed only in the
            Authorization: Bearer header. CredentialHandle.__repr__ is redacted.
  T-16-02: The Calendly API base URL is a FIXED MODULE CONSTANT (CALENDLY_API_BASE).
            No URL field exists in BookSlotInput or any other typed schema. The event_type
            URI comes from config_data, not from args.service_type directly.
  T-16-cal-paid: POST /invitees requires a paid Calendly plan (Pitfall 7). Free plans
            receive 403 Forbidden. The adapter calls response.raise_for_status() so the
            HTTP exception propagates to the dispatcher and is audited as an error.
            Deploy-time prerequisite: tenant must have a paid Calendly plan.
            Fallback documented in runbook (16-07): return GET /event_types/{uuid}/scheduling_url.

Paid-plan caveat (Pitfall 7, RESEARCH.md):
  The Calendly Scheduling API POST /invitees creates an event invitee programmatically
  (no UI redirect required). This endpoint requires a paid Calendly plan. On free plans,
  Calendly returns 403 Forbidden even with a valid PAT. The adapter surfaces this as a
  raise_for_status() exception — the dispatcher audits it. The deploy-time runbook (16-07)
  documents the paid-plan requirement and the scheduling_url fallback.

Calendly Scheduling API reference (LOW confidence — docs require paid plan to verify):
  POST https://api.calendly.com/invitees
  Authorization: Bearer <personal_access_token>
  {
    "event_type": "<event_type_uri>",
    "start_time": "<ISO 8601 datetime>",
    "invitee": {"name": "<customer_name>"}
  }
  [Source: developer.calendly.com/api-docs, LOW confidence — Pitfall 7 applies]
"""

from __future__ import annotations

import json

import httpx
import structlog

from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.provider_adapter import ProviderAdapter
from app.services.transactional.schemas import (
    BookSlotInput,
    BookSlotOutput,
    CancelOrderInput,
    CancelOrderOutput,
    IssueRefundInput,
    IssueRefundOutput,
    PlaceOrderInput,
    PlaceOrderOutput,
    UpdateCustomerRecordInput,
    UpdateCustomerRecordOutput,
    UpdateSubscriptionInput,
    UpdateSubscriptionOutput,
)

log = structlog.get_logger(__name__)

# Fixed Calendly API base URL — NEVER read from tool args or config (T-16-02: SSRF prevention)
CALENDLY_API_BASE = "https://api.calendly.com"


class CalendlyAdapter(ProviderAdapter):
    """Real Calendly provider adapter behind the typed tool contract (INT-06).

    Uses native async httpx (no asyncio.to_thread — httpx is async-native).
    The personal_access_token is extracted from the CredentialHandle and placed
    only in the Authorization: Bearer header. It is never logged (T-16-01).

    The event_type URI mapping (service_type label → Calendly event_type URI) is
    resolved from integration_credentials.config_data["event_types"] at __init__ time
    (Open Question 2 resolution — keeps BookSlotInput.service_type provider-agnostic).

    Usage (injected by get_adapter_for_skill in Plan 16-02):
        adapter = CalendlyAdapter(handle=handle, config_data=config.config_data)
        result = await adapter.book_slot(args, agent_id=agent_id)
    """

    def __init__(self, handle: CredentialHandle, config_data: dict | None = None) -> None:
        """Initialise the adapter with a resolved credential handle and per-tenant config.

        Args:
            handle: In-memory CredentialHandle wrapping the decrypted Calendly credential
                    JSON blob ({"personal_access_token": "eyJ..."}). Lifetime scoped to
                    tool call.
            config_data: Per-tenant integration config from integration_credentials.config_data.
                         Must contain an "event_types" sub-dict mapping service_type labels
                         (e.g. "consultation") to Calendly event_type URIs
                         (e.g. "https://api.calendly.com/event_types/<UUID>").
                         If None or missing "event_types", all book_slot calls raise ValueError
                         due to missing service_type mappings.

        Example config_data:
            {
                "event_types": {
                    "consultation": "https://api.calendly.com/event_types/AAABBB111",
                    "demo":         "https://api.calendly.com/event_types/CCCDDD222"
                }
            }
        """
        self._handle = handle
        # Extract event_types mapping from config_data (Open Question 2 resolution)
        # Resolves service_type (human label) → Calendly event_type URI at call time
        self._event_types: dict = (config_data or {}).get("event_types", {})

    # -----------------------------------------------------------------------
    # book_slot — POST /invitees (Calendly Scheduling API)
    # -----------------------------------------------------------------------

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        """Book a Calendly event slot via POST /invitees (Scheduling API, requires paid plan).

        Resolves args.service_type to a Calendly event_type URI via the config_data
        event_types mapping (Open Question 2). Raises ValueError if the service_type
        is not in the mapping (audited by dispatcher as a config error).

        The PAT is extracted from the CredentialHandle inside this method and placed
        only in the Authorization header. It is never logged (T-16-01).

        Paid-plan caveat (Pitfall 7): POST /invitees requires a paid Calendly plan.
        Free plans receive 403 Forbidden, which propagates via raise_for_status().

        Args:
            args.service_type: Human-readable service label (e.g. "consultation").
                               Resolved to a Calendly event_type URI via config_data.
            args.preferred_date: ISO 8601 date (YYYY-MM-DD).
            args.preferred_time: 24-hour time (HH:MM).
            args.customer_name: Full name of the customer for the booking.

        Returns:
            BookSlotOutput(booking_id=<resource uri>, status="confirmed", message=...)

        Raises:
            ValueError: If args.service_type is not in the config_data event_types mapping.
            httpx.HTTPStatusError: If the Calendly API returns a non-2xx status
                                   (e.g. 403 on free plans, Pitfall 7).
        """
        # Resolve service_type → event_type URI (Open Question 2)
        # Raises ValueError before any HTTP call if service_type is missing (config error)
        event_type_uri = self._event_types.get(args.service_type)
        if event_type_uri is None:
            available = list(self._event_types.keys())
            raise ValueError(
                f"CalendlyAdapter: unknown service_type {args.service_type!r}. "
                f"Available service types from config_data: {available}. "
                "Add the service_type → event_type URI mapping to integration_credentials.config_data."
            )

        # Build ISO 8601 start_time from preferred_date + preferred_time
        start_time = f"{args.preferred_date}T{args.preferred_time}:00Z"

        # Extract PAT inside the method — never stored at module or instance level (T-16-01)
        # json.loads extracts personal_access_token from the credential JSON blob
        pat = json.loads(self._handle.use())["personal_access_token"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={
                    "Authorization": f"Bearer {pat}",  # T-16-01: PAT in header only
                    "Content-Type": "application/json",
                },
                json={
                    "event_type": event_type_uri,  # T-16-02: from config, not args
                    "start_time": start_time,
                    "invitee": {"name": args.customer_name},
                },
            )
            # raise_for_status propagates 403 (paid-plan requirement, Pitfall 7)
            # and any other HTTP errors to the dispatcher for auditing
            response.raise_for_status()
            data = response.json()

        booking_id = data["resource"]["uri"]
        join_url = data["resource"].get("join_url", "N/A")

        log.info(
            "calendly.slot_booked",
            booking_id=booking_id,
            service_type=args.service_type,
            preferred_date=args.preferred_date,
            preferred_time=args.preferred_time,
            agent_id=agent_id,
            # NEVER log the PAT or CredentialHandle
        )
        return BookSlotOutput(
            booking_id=booking_id,
            status="confirmed",
            message=f"Booking confirmed for {args.service_type} on {args.preferred_date} at {args.preferred_time}. Join URL: {join_url}",
        )

    # -----------------------------------------------------------------------
    # Unsupported methods — raise NotImplementedError (fast-fail)
    # -----------------------------------------------------------------------

    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        """Not supported by CalendlyAdapter.

        Order placement is a WooCommerce/Shopify/Stripe concern.
        """
        raise NotImplementedError(
            "place_order not supported by CalendlyAdapter — "
            "use WooCommerceAdapter or ShopifyAdapter for order placement."
        )

    async def cancel_order(
        self, args: CancelOrderInput, agent_id: str
    ) -> CancelOrderOutput:
        """Not supported by CalendlyAdapter.

        Order cancellation is a WooCommerce/Shopify concern.
        """
        raise NotImplementedError(
            "cancel_order not supported by CalendlyAdapter — "
            "use WooCommerceAdapter or ShopifyAdapter for order cancellation."
        )

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        """Not supported by CalendlyAdapter.

        Refunds are a WooCommerce/Shopify/Stripe concern.
        """
        raise NotImplementedError(
            "issue_refund not supported by CalendlyAdapter — "
            "use WooCommerceAdapter, ShopifyAdapter, or StripeAdapter for refunds."
        )

    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        """Not supported by CalendlyAdapter.

        Subscription management is a Stripe concern.
        """
        raise NotImplementedError(
            "update_subscription not supported by CalendlyAdapter — "
            "use StripeAdapter for subscription management."
        )

    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        """Not supported by CalendlyAdapter (Phase 16 deferred per Provider→Tool Mapping)."""
        raise NotImplementedError(
            "update_customer_record not supported by CalendlyAdapter."
        )
