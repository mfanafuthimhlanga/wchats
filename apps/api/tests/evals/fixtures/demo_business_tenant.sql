-- Bella Vista Coffee — Demo Tenant SQL Fixture
-- Purpose: Seed documents/chunks/chunk_metadata/embeddings for M4 eval scenarios.
-- Assumes tenant DB already initialized via Alembic (0001_tenant_v1_schema.py).
-- Run: psycopg2.connect(tenant_conn_str).cursor().execute(open(this_file).read())
-- Idempotent: ON CONFLICT (id) DO NOTHING on all INSERTs.
-- Embeddings: zero vector(1024) — eval scenarios mock retrieval; these keep schema valid.

-- ---------------------------------------------------------------------------
-- Documents (6)
-- ---------------------------------------------------------------------------

INSERT INTO documents (id, source_type, source_uri, title, metadata, created_at)
VALUES
  ('a1000000-0000-0000-0000-000000000001', 'pdf', 's3://bella-vista/return-policy.pdf',
   'Return Policy', '{"version": "2.1"}', NOW()),
  ('a1000000-0000-0000-0000-000000000002', 'pdf', 's3://bella-vista/business-hours.pdf',
   'Business Hours', '{"version": "1.0"}', NOW()),
  ('a1000000-0000-0000-0000-000000000003', 'pdf', 's3://bella-vista/product-catalog.pdf',
   'Product Catalog', '{"version": "3.0"}', NOW()),
  ('a1000000-0000-0000-0000-000000000004', 'pdf', 's3://bella-vista/pricing.pdf',
   'Pricing Guide', '{"version": "1.2"}', NOW()),
  ('a1000000-0000-0000-0000-000000000005', 'pdf', 's3://bella-vista/contact-info.pdf',
   'Contact Information', '{"version": "1.0"}', NOW()),
  ('a1000000-0000-0000-0000-000000000006', 'pdf', 's3://bella-vista/shipping-policy.pdf',
   'Shipping Policy', '{"version": "1.1"}', NOW())
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Chunks (18 total — 3 per document)
-- ---------------------------------------------------------------------------

INSERT INTO chunks (id, document_id, ordinal, content, token_count, created_at)
VALUES
  -- Return Policy chunks
  ('b1000000-0000-0000-0000-000000000001', 'a1000000-0000-0000-0000-000000000001', 1,
   'Bella Vista Coffee accepts returns within 14 days of purchase for a full refund. Items must be unused and in original packaging. Coffee beans that have been opened are not eligible for return due to freshness concerns.', 42, NOW()),
  ('b1000000-0000-0000-0000-000000000002', 'a1000000-0000-0000-0000-000000000001', 2,
   'To initiate a return, customers must contact our support team with their order number and reason for return. We will issue a prepaid return shipping label within 2 business days. Refunds are processed within 5-7 business days of receiving the returned item.', 48, NOW()),
  ('b1000000-0000-0000-0000-000000000003', 'a1000000-0000-0000-0000-000000000001', 3,
   'Defective or damaged items are eligible for an immediate replacement or full refund regardless of the 14-day window. Please photograph the damage before contacting support. Wholesale orders follow a separate returns process outlined in the wholesale agreement.', 46, NOW()),

  -- Business Hours chunks
  ('b1000000-0000-0000-0000-000000000004', 'a1000000-0000-0000-0000-000000000002', 1,
   'Bella Vista Coffee main store hours: Monday through Friday 7:00 AM to 7:00 PM, Saturday 8:00 AM to 6:00 PM, Sunday 9:00 AM to 5:00 PM. We are closed on all federal holidays.', 36, NOW()),
  ('b1000000-0000-0000-0000-000000000005', 'a1000000-0000-0000-0000-000000000002', 2,
   'Our online store and customer support team are available Monday through Friday 8:00 AM to 6:00 PM Pacific Time. Email support responses are typically provided within 4 business hours during operating hours.', 38, NOW()),
  ('b1000000-0000-0000-0000-000000000006', 'a1000000-0000-0000-0000-000000000002', 3,
   'Holiday hours vary — please check our website or social media for updates during Thanksgiving, Christmas, and New Year periods. Drive-through window hours match main store hours.', 32, NOW()),

  -- Product Catalog chunks
  ('b1000000-0000-0000-0000-000000000007', 'a1000000-0000-0000-0000-000000000003', 1,
   'Bella Vista Coffee signature blends include: House Blend (medium roast, notes of chocolate and caramel), Sunrise Espresso (dark roast, bold and intense), and Morning Mist (light roast, bright and fruity). All blends are available in whole bean and ground varieties.', 44, NOW()),
  ('b1000000-0000-0000-0000-000000000008', 'a1000000-0000-0000-0000-000000000003', 2,
   'Single-origin offerings rotate seasonally. Current offerings include Ethiopian Yirgacheffe (fruity, floral), Colombian Huila (nutty, balanced), and Guatemalan Antigua (smoky, rich). Limited quantities available; subscribe to our newsletter for restocking alerts.', 42, NOW()),
  ('b1000000-0000-0000-0000-000000000009', 'a1000000-0000-0000-0000-000000000003', 3,
   'Bella Vista also offers cold brew concentrates, flavored syrups (vanilla, hazelnut, caramel), and brewing accessories including French press, pour-over kits, and electric grinders. Gift sets are available year-round.', 38, NOW()),

  -- Pricing chunks
  ('b1000000-0000-0000-0000-000000000010', 'a1000000-0000-0000-0000-000000000004', 1,
   'Standard 12 oz bags: House Blend $14.99, Sunrise Espresso $15.99, Morning Mist $14.99. Single-origin 12 oz bags: $18.99-$24.99 depending on origin and availability. Bulk 5 lb bags available at 20% discount.', 40, NOW()),
  ('b1000000-0000-0000-0000-000000000011', 'a1000000-0000-0000-0000-000000000004', 2,
   'Subscriptions save 15% on every order. Monthly subscription options: 1 bag/month, 2 bags/month, or 4 bags/month. Subscriptions can be paused or cancelled at any time with no fees. First subscription order ships free.', 38, NOW()),
  ('b1000000-0000-0000-0000-000000000012', 'a1000000-0000-0000-0000-000000000004', 3,
   'Cafe drinks pricing: Espresso $3.50, Americano $4.00, Latte $5.50, Cappuccino $5.00, Cold Brew $5.50, Seasonal specials $6.00-$7.00. Oat milk and almond milk available at $0.75 surcharge.', 36, NOW()),

  -- Contact Info chunks
  ('b1000000-0000-0000-0000-000000000013', 'a1000000-0000-0000-0000-000000000005', 1,
   'Bella Vista Coffee headquarters: 1420 Coffee Lane, Portland, OR 97201. Customer support email: support@bellavistacoffee.com. Phone: (503) 555-0178. Support hours: Monday-Friday 8AM-6PM PT.', 36, NOW()),
  ('b1000000-0000-0000-0000-000000000014', 'a1000000-0000-0000-0000-000000000005', 2,
   'For wholesale inquiries contact: wholesale@bellavistacoffee.com. For press and media: media@bellavistacoffee.com. Our social media handles are @bellavistacoffee on Instagram, Twitter, and Facebook.', 32, NOW()),
  ('b1000000-0000-0000-0000-000000000015', 'a1000000-0000-0000-0000-000000000005', 3,
   'Physical store address: 1420 Coffee Lane, Portland, OR 97201. We also have a drive-through at 890 Roast Ave, Portland, OR 97202. Parking is available in the adjacent lot — first 30 minutes free with any purchase.', 38, NOW()),

  -- Shipping Policy chunks
  ('b1000000-0000-0000-0000-000000000016', 'a1000000-0000-0000-0000-000000000006', 1,
   'Standard shipping (5-7 business days): Free on orders over $40, $5.99 on orders under $40. Expedited shipping (2-3 business days): $12.99. Overnight shipping: $24.99. All orders ship from Portland, OR.', 38, NOW()),
  ('b1000000-0000-0000-0000-000000000017', 'a1000000-0000-0000-0000-000000000006', 2,
   'Orders placed before 2:00 PM PT on business days ship the same day. Orders after 2:00 PM PT or on weekends ship the next business day. Tracking information is emailed within 1 hour of shipment.', 36, NOW()),
  ('b1000000-0000-0000-0000-000000000018', 'a1000000-0000-0000-0000-000000000006', 3,
   'International shipping is available to Canada, UK, and Australia. International orders ship via DHL Express (7-14 business days). International shipping rates start at $29.99. Customs duties are the responsibility of the recipient.', 38, NOW())
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- chunk_metadata (18 rows — one per chunk)
-- ---------------------------------------------------------------------------

INSERT INTO chunk_metadata (chunk_id, summary, keywords, questions, created_at)
VALUES
  ('b1000000-0000-0000-0000-000000000001', 'Return window is 14 days; opened coffee not returnable.',
   ARRAY['return', 'refund', '14 days', 'unopened', 'packaging'],
   ARRAY['What is the return policy?', 'Can I return coffee?', 'How long do I have to return?'], NOW()),
  ('b1000000-0000-0000-0000-000000000002', 'Return process requires order number; refund in 5-7 days.',
   ARRAY['return process', 'order number', 'refund', 'shipping label'],
   ARRAY['How do I start a return?', 'How long does a refund take?'], NOW()),
  ('b1000000-0000-0000-0000-000000000003', 'Defective items get immediate replacement regardless of window.',
   ARRAY['defective', 'damaged', 'replacement', 'wholesale'],
   ARRAY['What if my item is damaged?', 'Can I return a defective item?'], NOW()),
  ('b1000000-0000-0000-0000-000000000004', 'Store open Mon-Fri 7am-7pm, Sat 8am-6pm, Sun 9am-5pm.',
   ARRAY['hours', 'open', 'schedule', 'Monday', 'Saturday', 'Sunday'],
   ARRAY['What are your hours?', 'When are you open?', 'Are you open Sunday?'], NOW()),
  ('b1000000-0000-0000-0000-000000000005', 'Online support Mon-Fri 8am-6pm PT; 4-hour email response.',
   ARRAY['support', 'online', 'email', 'Pacific Time'],
   ARRAY['When is support available?', 'How fast do you respond to email?'], NOW()),
  ('b1000000-0000-0000-0000-000000000006', 'Holiday hours vary; check website.',
   ARRAY['holiday', 'Christmas', 'Thanksgiving', 'hours'],
   ARRAY['Are you open on holidays?', 'What are holiday hours?'], NOW()),
  ('b1000000-0000-0000-0000-000000000007', 'Three signature blends: House, Sunrise Espresso, Morning Mist.',
   ARRAY['blend', 'roast', 'house blend', 'espresso', 'medium', 'dark', 'light'],
   ARRAY['What coffee do you sell?', 'Do you have light roast?'], NOW()),
  ('b1000000-0000-0000-0000-000000000008', 'Seasonal single-origin coffees from Ethiopia, Colombia, Guatemala.',
   ARRAY['single-origin', 'Ethiopia', 'Colombia', 'Guatemala', 'seasonal'],
   ARRAY['Do you have single-origin coffee?', 'What origin coffees do you carry?'], NOW()),
  ('b1000000-0000-0000-0000-000000000009', 'Also sells cold brew, syrups, accessories, and gift sets.',
   ARRAY['cold brew', 'syrup', 'accessories', 'gift set', 'French press'],
   ARRAY['Do you sell equipment?', 'Do you have gift sets?'], NOW()),
  ('b1000000-0000-0000-0000-000000000010', 'Bags $14.99-$24.99; bulk 5lb at 20% discount.',
   ARRAY['price', 'cost', '12 oz', '5 lb', 'bulk', 'discount'],
   ARRAY['How much does coffee cost?', 'What is the price?'], NOW()),
  ('b1000000-0000-0000-0000-000000000011', 'Subscriptions save 15%; pause or cancel anytime.',
   ARRAY['subscription', '15%', 'monthly', 'pause', 'cancel'],
   ARRAY['Do you have a subscription?', 'How much is a subscription?'], NOW()),
  ('b1000000-0000-0000-0000-000000000012', 'Cafe drinks $3.50-$7.00; oat/almond milk $0.75 extra.',
   ARRAY['cafe', 'latte', 'espresso', 'cappuccino', 'oat milk', 'price'],
   ARRAY['How much is a latte?', 'What does a coffee cost at the cafe?'], NOW()),
  ('b1000000-0000-0000-0000-000000000013', 'HQ in Portland OR; support@bellavistacoffee.com; (503) 555-0178.',
   ARRAY['contact', 'email', 'phone', 'Portland', 'address'],
   ARRAY['How do I contact you?', 'What is your phone number?', 'What is your email?'], NOW()),
  ('b1000000-0000-0000-0000-000000000014', 'Wholesale and press contacts available.',
   ARRAY['wholesale', 'press', 'media', 'social media', 'Instagram'],
   ARRAY['How do I contact wholesale?', 'What is your Instagram?'], NOW()),
  ('b1000000-0000-0000-0000-000000000015', 'Two physical locations in Portland; 30 min free parking.',
   ARRAY['store', 'location', 'address', 'parking', 'drive-through'],
   ARRAY['Where is your store?', 'Do you have parking?'], NOW()),
  ('b1000000-0000-0000-0000-000000000016', 'Standard shipping free over $40; expedited $12.99; overnight $24.99.',
   ARRAY['shipping', 'cost', 'free', 'expedited', 'overnight'],
   ARRAY['How much is shipping?', 'Do you offer free shipping?'], NOW()),
  ('b1000000-0000-0000-0000-000000000017', 'Orders before 2 PM PT ship same day; tracking emailed.',
   ARRAY['ship', 'same day', '2 PM', 'tracking', 'business day'],
   ARRAY['When does my order ship?', 'How do I track my order?'], NOW()),
  ('b1000000-0000-0000-0000-000000000018', 'International shipping to Canada, UK, Australia via DHL.',
   ARRAY['international', 'Canada', 'UK', 'Australia', 'DHL', 'customs'],
   ARRAY['Do you ship internationally?', 'Do you ship to Canada?'], NOW())
ON CONFLICT (chunk_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- embeddings (18 rows — zero vector(1024) for schema consistency)
-- Eval scenarios mock retrieval; these keep FK constraints valid.
-- ---------------------------------------------------------------------------

INSERT INTO embeddings (chunk_id, model, vector, created_at)
SELECT
  id AS chunk_id,
  'voyage-3' AS model,
  ('[' || repeat('0,', 1023) || '0]')::vector AS vector,
  NOW() AS created_at
FROM chunks
WHERE id LIKE 'b1000000-%'
ON CONFLICT (chunk_id) DO NOTHING;
