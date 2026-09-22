// The flagged claims the review page shows under NEXT_PUBLIC_DEMO=true, the
// same seam agents/page.tsx uses for its two demo agents. The smoke and pixel
// passes render this; no API is reached.

import type { FlaggedClaimsResponse } from './sitting'

const RETURNS =
  'Returns and refunds. You can return an unused item within 30 days of delivery for a full refund. ' +
  'Refunds are paid to the original payment method and arrive within 5 to 7 working days of us receiving the item. ' +
  'Items marked final sale cannot be returned. To start a return, open your order in the account page and choose Return item. ' +
  'We email a prepaid courier label the same day. Sale items can be returned for store credit only.'

const SHIPPING =
  'Delivery. Standard delivery takes 2 to 4 working days to main centres and 3 to 6 working days to outlying areas. ' +
  'Orders over R500 ship free. Orders under R500 carry a flat R60 delivery fee. ' +
  'We do not deliver to PO boxes. Same-day delivery is available in Johannesburg and Cape Town for orders placed before 11:00.'

export const DEMO_FLAGGED: FlaggedClaimsResponse = {
  scenarios: [
    {
      scenario_id: 'demo-returns',
      question: 'How long do I have to return something, and how long does the refund take?',
      response:
        'You can return an unused item within 30 days of delivery. Once we receive it, the refund goes back to your original payment method and arrives within 5 to 7 working days.\n\n' +
        'Items marked final sale cannot be returned, and refunds on sale items are paid as store credit. ' +
        'If the item arrived damaged we also refund the original delivery fee.',
      retrieved_contexts: [RETURNS, SHIPPING],
      claims: [
        { position: 3, statement: 'A damaged item earns a refund of the original delivery fee.', review: null },
        { position: 2, statement: 'Refunds on sale items are paid as store credit.', review: null },
      ],
    },
    {
      scenario_id: 'demo-shipping',
      question: 'Do you deliver to PO boxes, and what does delivery cost?',
      response:
        'We do not deliver to PO boxes. Orders over R500 ship free; under R500 there is a flat R60 fee. ' +
        'Standard delivery takes 2 to 4 working days to main centres. Weekend delivery is available for R120.',
      retrieved_contexts: [SHIPPING],
      claims: [{ position: 3, statement: 'Weekend delivery is available for R120.', review: true }],
    },
    {
      scenario_id: 'demo-warranty',
      question: 'Is there a warranty on electronics?',
      response: 'Every electronic item carries a 12 month manufacturer warranty. Extended cover to 24 months can be bought at checkout.',
      retrieved_contexts: [],
      claims: [
        { position: 0, statement: 'Every electronic item carries a 12 month manufacturer warranty.', review: null },
        { position: 1, statement: 'Extended cover to 24 months can be bought at checkout.', review: false },
      ],
    },
  ],
  flagged: 5,
  answered: 2,
  reviews_available: true,
  dropped_scenarios: 0,
}
