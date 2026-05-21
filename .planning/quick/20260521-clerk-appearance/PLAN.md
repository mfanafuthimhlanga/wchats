---
phase: quick-20260521-clerk-appearance
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - apps/admin/app/layout.tsx
  - apps/admin/app/components/TopNav.tsx
  - apps/admin/app/components/UserAvatar.tsx
autonomous: true
requirements:
  - clerk-appearance-design-system-match

must_haves:
  truths:
    - "ClerkProvider variables override Clerk's default palette with the Veridian wine/cream design system"
    - "Clerk modal cards (sign-in, sign-up, user profile) render with the correct background, border, radius, and button styles"
    - "UserButton in TopNav shows a black-background avatar with a red user icon overlay, not the bare Clerk avatar"
    - "UserButton popover inherits wine/cream palette (bg, border, action button hover)"
  artifacts:
    - path: "apps/admin/app/layout.tsx"
      provides: "ClerkProvider with full appearance prop (variables + elements)"
    - path: "apps/admin/app/components/UserAvatar.tsx"
      provides: "Client component wrapping UserButton with absolute-positioned red User icon overlay"
    - path: "apps/admin/app/components/TopNav.tsx"
      provides: "Replaces bare <UserButton> with <UserAvatar>"
  key_links:
    - from: "apps/admin/app/layout.tsx"
      to: "@clerk/nextjs ClerkProvider"
      via: "appearance prop passed to provider — scopes all Clerk UI globally"
    - from: "apps/admin/app/components/TopNav.tsx"
      to: "apps/admin/app/components/UserAvatar.tsx"
      via: "import and render <UserAvatar /> in place of <UserButton />"
---

<objective>
Wire Clerk appearance config so every Clerk-rendered surface (modals, UserButton, popover) matches the Veridian wine/cream design system, and replace the bare Clerk avatar with a branded black-bg + red-icon avatar in the TopNav.

Purpose: Visual consistency — Clerk UI must look native to the admin shell, not default Clerk blue.
Output: Updated layout.tsx (ClerkProvider appearance), new UserAvatar.tsx client component, updated TopNav.tsx.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@apps/admin/app/layout.tsx
@apps/admin/app/components/TopNav.tsx
@apps/admin/app/globals.css

Design tokens in play (from globals.css):
  --accent: #7B1C3A          (wine primary)
  --accent-hover: #5E1229    (wine hover)
  --surface-1: #FFFCF9       (card background)
  --surface-2: #F7F0EA       (input background)
  --border: #D9CCBE          (card/input border)
  --text-1: #1A0A0F          (primary text)
  --text-2: #4A2030          (secondary text)
  --border-soft: #EDE3D8     (popover footer separator)
  --red: #B91C1C             (danger / error)
  --shadow-card: 0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06)
  --radius-xs: 8px
  --radius-sm: 14px
  --radius-md: 20px

Key findings from codebase read:
  - layout.tsx is a Server Component — appearance object must be a plain JS literal (no hooks, no CSS vars at runtime).
  - TopNav.tsx is 'use client' and already renders <UserButton appearance={{ elements: { avatarBox: ... } }}>.
  - agents/layout.tsx wraps every agents/* page with <TopNav />, so TopNav is the correct placement.
  - No existing UserAvatar component — create it fresh.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add appearance prop to ClerkProvider in layout.tsx</name>
  <files>apps/admin/app/layout.tsx</files>
  <action>
Edit apps/admin/app/layout.tsx. Add an `appearance` prop to `<ClerkProvider>` with a plain object literal (no imports, no CSS vars — Server Component, values must be static strings/numbers).

The object shape is `{ variables, elements }`:

variables block (maps to Clerk's CSS custom property overrides):
  colorPrimary: '#7B1C3A'
  colorBackground: '#FFFCF9'
  colorNeutral: '#4A2030'
  colorText: '#1A0A0F'
  colorTextSecondary: '#4A2030'
  colorInputBackground: '#F7F0EA'
  colorInputText: '#1A0A0F'
  colorDanger: '#B91C1C'
  borderRadius: '14px'
  fontFamily: 'Inter, system-ui, sans-serif'

elements block (inline style objects applied to Clerk element selectors):
  card:
    background: '#FFFCF9'
    border: '1px solid #D9CCBE'
    boxShadow: '0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06)'
    borderRadius: '20px'

  formButtonPrimary:
    background: '#7B1C3A'
    color: '#ffffff'
    Clerk applies hover via its own CSS; the variable colorPrimary handles the hover tint — no separate hover key needed here.

  formFieldInput:
    background: '#F7F0EA'
    border: '1px solid #D9CCBE'
    color: '#1A0A0F'
    borderRadius: '8px'

  userButtonAvatarBox:
    width: '32px'
    height: '32px'
    background: '#1A0A0F'
    borderRadius: '8px'
    overflow: 'hidden'

  userButtonAvatarImage:
    opacity: '0'
    -- This hides the Clerk-fetched user photo so the custom icon overlay in UserAvatar.tsx is visible.

  userButtonTrigger:
    background: '#1A0A0F'
    borderRadius: '8px'
    padding: '4px'

  userButtonPopoverCard:
    background: '#FFFCF9'
    border: '1px solid #D9CCBE'
    boxShadow: '0 4px 16px rgba(26,10,15,0.12)'

  userButtonPopoverActionButton:
    color: '#4A2030'

  userButtonPopoverFooter:
    borderTop: '1px solid #EDE3D8'

  avatarBox:
    width: '32px'
    height: '32px'

The ClerkProvider JSX line currently is:
  `<ClerkProvider>`
Replace with:
  `<ClerkProvider appearance={clerkAppearance}>`

Define `const clerkAppearance = { variables: { ... }, elements: { ... } }` as a module-level constant above the RootLayout function so the object is not recreated per render. TypeScript type: `import type { Appearance } from '@clerk/nextjs/server'` — add this import. If the Appearance type is not exported from that path, use `import type { Appearance } from '@clerk/types'` instead (both are valid in @clerk/nextjs ^7).
  </action>
  <verify>
    <automated>cd apps/admin && pnpm tsc --noEmit 2>&1 | head -40</automated>
  </verify>
  <done>
TypeScript reports zero errors in layout.tsx. The ClerkProvider element in the file now has an `appearance={clerkAppearance}` prop. The clerkAppearance constant contains both `variables` and `elements` keys.
  </done>
</task>

<task type="auto">
  <name>Task 2: Create UserAvatar client component with red icon overlay</name>
  <files>apps/admin/app/components/UserAvatar.tsx</files>
  <action>
Create apps/admin/app/components/UserAvatar.tsx as a 'use client' component.

Goal: render the Clerk UserButton but show a black-background box with a red User icon on top, instead of the user's profile photo (which is hidden via opacity:0 in the ClerkProvider appearance).

Structure:
  - Outer div: position relative, display inline-flex, alignItems center, justifyContent center.
  - Inside: render `<UserButton />` from '@clerk/nextjs' with no additional appearance prop (the global ClerkProvider appearance handles the avatar styling — userButtonAvatarBox, userButtonAvatarImage, userButtonTrigger).
  - Also inside: a second div positioned absolute, pointerEvents none, with a `<User>` icon from 'lucide-react' (size=16, color='#ef4444'). This div must be centered over the trigger button area.

Positioning specifics:
  - Outer wrapper: `{ position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }`
  - Icon overlay div: `{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', zIndex: 1 }`

The UserButton itself sits at zIndex 0 (default stacking). The icon sits at zIndex 1, pointer-events none so Clerk's click handler on the trigger button still fires.

Import: `import { UserButton } from '@clerk/nextjs'` and `import { User } from 'lucide-react'`.

No props needed — this component is a styled singleton.

Export as default.
  </action>
  <verify>
    <automated>cd apps/admin && pnpm tsc --noEmit 2>&1 | head -40</automated>
  </verify>
  <done>
File exists at apps/admin/app/components/UserAvatar.tsx. TypeScript reports zero errors. File exports a default React component that renders UserButton and a lucide User icon overlay.
  </done>
</task>

<task type="auto">
  <name>Task 3: Replace UserButton with UserAvatar in TopNav</name>
  <files>apps/admin/app/components/TopNav.tsx</files>
  <action>
Edit apps/admin/app/components/TopNav.tsx.

1. Remove the import of `UserButton` from '@clerk/nextjs'.
2. Add import: `import UserAvatar from './UserAvatar'`
3. In the JSX, find the current UserButton render:
     `<UserButton appearance={{ elements: { avatarBox: { width: 32, height: 32 } } }} />`
   Replace with:
     `<UserAvatar />`

No other changes to TopNav. The nav layout, logo, links, and sticky positioning remain unchanged.
  </action>
  <verify>
    <automated>cd apps/admin && pnpm tsc --noEmit 2>&1 | head -40</automated>
  </verify>
  <done>
TopNav.tsx imports UserAvatar (not UserButton). The JSX renders `<UserAvatar />` in the user avatar slot. `pnpm tsc --noEmit` reports zero errors across the admin app.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Browser -> Clerk hosted UI | Appearance config is cosmetic only — no auth logic or secrets involved |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-clerk-01 | Information Disclosure | clerkAppearance constant | accept | Contains only public color hex values — no secrets, no env vars, no PII |
| T-clerk-02 | Tampering | userButtonAvatarImage opacity:0 | accept | Purely visual; Clerk authentication token/session is unaffected by CSS overrides |
</threat_model>

<verification>
After all three tasks complete and each is committed:

1. Start the admin dev server: `cd apps/admin && pnpm dev`
2. Visit http://localhost:3000/agents (or the configured port).
3. TopNav avatar shows a black square with a small red user icon — no Clerk profile photo visible.
4. Clicking the avatar opens the Clerk UserButton popover: background #FFFCF9, border #D9CCBE, action buttons in wine/maroon text.
5. Navigate to sign-in (sign out first if needed): the Clerk card has bg #FFFCF9, 20px radius, wine primary button, cream input fields.
6. `pnpm tsc --noEmit` from apps/admin exits 0.
</verification>

<success_criteria>
- ClerkProvider in layout.tsx has `appearance` prop with `variables` and `elements` keys covering all nine element selectors from the spec.
- UserAvatar.tsx exists and renders a UserButton + red User icon overlay with pointer-events:none on the icon.
- TopNav.tsx uses `<UserAvatar />` instead of the bare `<UserButton />`.
- Zero TypeScript errors across the admin app.
- Three atomic git commits: one per task.
</success_criteria>

<output>
Each task gets its own commit message:

Task 1: `feat(admin): add Clerk appearance config to ClerkProvider for design system match`
Task 2: `feat(admin): add UserAvatar client component with red icon overlay`
Task 3: `feat(admin): replace bare UserButton with UserAvatar in TopNav`

No SUMMARY.md required — this is a quick plan.
</output>
