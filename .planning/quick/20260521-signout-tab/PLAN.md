---
phase: quick-20260521-signout-tab
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - apps/admin/package.json
  - apps/admin/app/components/SignOutTab.tsx
  - apps/admin/app/globals.css
  - apps/admin/app/layout.tsx
autonomous: false
requirements: [signout-tab]

must_haves:
  truths:
    - "A narrow tab peeks 8px from the right edge of every admin page"
    - "Hovering the tab slides it fully into view"
    - "Clicking the tab triggers a 3D page-flip animation then signs the user out"
    - "After sign-out the browser lands on /"
  artifacts:
    - path: "apps/admin/app/components/SignOutTab.tsx"
      provides: "Client component with Clerk signOut + page-flip trigger"
    - path: "apps/admin/app/globals.css"
      provides: "page-flip keyframe + .sign-out-tab styles"
    - path: "apps/admin/app/layout.tsx"
      provides: "SignOutTab mounted inside ClerkProvider on every page"
  key_links:
    - from: "apps/admin/app/components/SignOutTab.tsx"
      to: "html.page-flip CSS class"
      via: "document.documentElement.classList.add('page-flip')"
    - from: "SignOutTab click handler"
      to: "Clerk signOut"
      via: "useClerk().signOut({ redirectUrl: '/' }) after 550ms"
---

<objective>
Add a sign-out tab that floats at the bottom-right of every admin page.
The tab peeks 8 px from the right edge and slides fully into view on hover.
Clicking it plays a CSS 3D page-flip animation on <html>, then calls Clerk
signOut and redirects to /.

Purpose: Gives authenticated admin users a persistent, accessible way to sign
out without navigating to a dedicated page.
Output: lucide-react installed; SignOutTab component, CSS, and layout wiring
all finalised and committed.
</objective>

<execution_context>
@C:\Users\Bantu\.claude\get-shit-done\workflows\execute-plan.md
</execution_context>

<context>
@C:\Users\Bantu\mzansi-agentive\veridian\.planning\PROJECT.md
@C:\Users\Bantu\mzansi-agentive\veridian\apps\admin\app\layout.tsx
@C:\Users\Bantu\mzansi-agentive\veridian\apps\admin\app\globals.css
@C:\Users\Bantu\mzansi-agentive\veridian\apps\admin\app\components\SignOutTab.tsx
</context>

<tasks>

<task type="auto">
  <name>Task 1: Install lucide-react in the admin workspace</name>
  <files>apps/admin/package.json</files>
  <action>
From the repo root, run:

    pnpm --filter veridian-admin add lucide-react

This adds lucide-react to apps/admin/package.json dependencies. Do not use npm
or yarn. Do not add it to the root package.json.

After the install completes, confirm the entry appears in
apps/admin/package.json under "dependencies" and that
node_modules/lucide-react exists inside apps/admin (or the pnpm virtual
store — either is fine as long as the import resolves).

Commit:
    git add apps/admin/package.json pnpm-lock.yaml
    git commit -m "chore(admin): add lucide-react dependency"
  </action>
  <verify>
    <automated>node -e "require.resolve('lucide-react')" --prefix apps/admin 2>&amp;&amp; echo ok || (cd apps/admin &amp;&amp; node -e "require.resolve('lucide-react')")</automated>
  </verify>
  <done>lucide-react appears in apps/admin/package.json "dependencies" and the
package resolves from within the admin workspace.</done>
</task>

<task type="auto">
  <name>Task 2: Finalise SignOutTab component, CSS, and layout wiring</name>
  <files>
    apps/admin/app/components/SignOutTab.tsx
    apps/admin/app/globals.css
    apps/admin/app/layout.tsx
  </files>
  <action>
All three draft files already contain the correct implementation. Read each
file and apply the following validation checks. Fix anything that deviates;
leave correct code untouched.

--- SignOutTab.tsx checks ---
- 'use client' directive on line 1 (required — useClerk is a client hook).
- Imports: `useClerk` from '@clerk/nextjs', `LogOut` from 'lucide-react',
  `useState` from 'react'. No other imports needed.
- `active` guard (`if (active) return`) prevents double-triggering.
- Adds 'page-flip' class to `document.documentElement` (not `document.body`).
- Calls `signOut({ redirectUrl: '/' })` inside `setTimeout(..., 550)`.
- Button has `aria-label="Sign out"` and `className="sign-out-tab"`.
- LogOut icon: `size={18}`, `strokeWidth={2.5}`,
  `style={{ transform: 'rotate(180deg)', color: '#ef4444' }}`.

--- globals.css checks ---
The sign-out tab block (lines ~142–207 of the current file) must contain:
1. `@keyframes page-flip` animating `perspective(1200px) rotateY(0deg)` →
   `rotateY(-90deg)` with opacity 1→0 over 0.55 s.
2. `html.page-flip` rule: applies the page-flip animation, `transform-origin:
   left center`, forwards fill.
3. `.sign-out-tab` rule: `position: fixed`, `bottom: 48px`, `right: 0`,
   `z-index: 9999`, `transform: translateX(calc(100% - 8px))`, `transition`
   on transform + box-shadow, `width: 40px`, `height: 52px`, dark background
   `#1A0A0F`, `border-radius: 8px 0 0 8px`, `border-right: none`.
4. `.sign-out-tab::before` — 2 px red left-edge accent stripe.
5. `.sign-out-tab:hover` — `transform: translateX(0)`.
6. `.sign-out-tab:focus-visible` — `transform: translateX(0)` + red outline.
7. `@media (prefers-reduced-motion: reduce)` block that disables both the
   page-flip animation and the tab transition.

--- layout.tsx checks ---
- `SignOutTab` is imported from `'./components/SignOutTab'`.
- `<SignOutTab />` is rendered as a sibling to `{children}` inside
  `<ClerkProvider>`, NOT outside it (SignOutTab uses useClerk which requires
  the provider).
- No other changes to layout.tsx.

After all checks pass, commit:
    git add apps/admin/app/components/SignOutTab.tsx \
            apps/admin/app/globals.css \
            apps/admin/app/layout.tsx
    git commit -m "feat(admin): add sign-out tab — peekaboo panel with page-flip animation"
  </action>
  <verify>
    <automated>cd C:\Users\Bantu\mzansi-agentive\veridian\apps\admin &amp;&amp; pnpm build 2>&amp;1 | tail -20</automated>
  </verify>
  <done>`pnpm build` exits 0 with no TypeScript or import errors related to
SignOutTab, lucide-react, or useClerk.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <what-built>
    Sign-out tab component mounted on every admin page. The tab is a narrow
    dark strip with a red LogOut icon, fixed to the bottom-right edge,
    visible as an 8 px sliver. Hover slides it fully into view. Click fires a
    550 ms 3D page-flip then signs out.
  </what-built>
  <how-to-verify>
    1. From apps/admin, run: pnpm dev
    2. Open http://localhost:3000 in a browser and sign in if prompted.
    3. Navigate to any admin page (e.g. /agents).
    4. Look at the bottom-right corner — confirm a narrow dark strip is just
       barely visible (~8px sliver).
    5. Hover the sliver — the full 40×52 px dark tab with the red arrow icon
       should slide into view smoothly.
    6. Move the cursor away — it should retract to 8 px.
    7. Click the tab — confirm the entire page starts a 3D fold/flip animation
       (the viewport rotates away on the Y axis), then the browser redirects
       to / (the home / sign-in page).
    8. Confirm you are signed out (returning to an admin route should require
       sign-in again).
    9. Optional accessibility check: Tab-key to the button and press Enter —
       same flow should trigger.
  </how-to-verify>
  <resume-signal>Type "approved" when sign-out works end-to-end, or describe any visual or functional issues.</resume-signal>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| browser → Clerk | signOut call crosses client → Clerk auth service boundary |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-sot-01 | Spoofing | SignOutTab click handler | accept | Clerk JWT/session is invalidated server-side by Clerk on signOut; client-side only triggers the call |
| T-sot-02 | Denial of Service | `active` guard in handleSignOut | mitigate | The `if (active) return` guard prevents spamming signOut; single in-flight call only |
| T-sot-03 | Information Disclosure | page-flip CSS on `<html>` | accept | Animation is purely visual; no sensitive data is revealed |
</threat_model>

<verification>
- `pnpm build` exits 0 in apps/admin
- No TypeScript errors on SignOutTab.tsx (`'use client'`, types from @clerk/nextjs, lucide-react)
- Tab visible as 8px sliver on every authenticated admin page
- Hover slides tab into full view; mouse-out retracts it
- Click triggers page-flip animation then redirects to /
- User session is invalidated after redirect (protected routes require re-auth)
- `prefers-reduced-motion` disables animation and transition without breaking signOut
</verification>

<success_criteria>
A signed-in admin user can sign out from any page by hovering and clicking the
bottom-right tab. The page-flip animation plays, Clerk ends the session, and
the browser lands on /. The feature ships with zero new backend endpoints.
</success_criteria>

<output>
No SUMMARY file required for quick plans. Mark done in conversation.
</output>
