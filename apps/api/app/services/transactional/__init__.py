"""
transactional — Typed tool contract for the 6 mutating tools + confirm_action.

Sub-modules:
    schemas         — 14 Pydantic v2 models (6 Input + 6 Output + ConfirmActionInput/Output)
    registry        — TransactionalToolDef dataclass + TOOL_METADATA / TOOL_REGISTRY dict
    provider_adapter — ProviderAdapter ABC + StubProviderAdapter + get_adapter()

Phase-15 seam (actor_seam.py in services/) is defined at the services level, not inside
this package, because it is called from services.transactional.tools but also needs to be
accessible to Phase 15 without importing the full transactional stack.
"""
