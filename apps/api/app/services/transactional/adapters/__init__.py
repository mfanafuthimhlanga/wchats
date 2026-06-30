"""
transactional.adapters — Concrete ProviderAdapter implementations.

Sub-modules (Wave 2, Plans 16-02 through 16-05):
    stripe_adapter     — StripeAdapter(ProviderAdapter)
    shopify_adapter    — ShopifyAdapter(ProviderAdapter)
    woocommerce_adapter — WooCommerceAdapter(ProviderAdapter)
    calendly_adapter   — CalendlyAdapter(ProviderAdapter)

This package init is intentionally empty: importing it here would cause
premature import errors if provider SDKs (stripe, ShopifyAPI, etc.) are not
yet installed. Wave 2 modules are imported lazily inside get_adapter_for_skill().
"""
