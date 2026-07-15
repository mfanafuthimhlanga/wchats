# Deferred Items

Out-of-scope discoveries logged during plan execution, not fixed (per deviation rule scope boundary).

## From 20-14 (dusk cutover)

- **`apps/admin/public/logo-mark.svg`** — still hardcodes the dusk-era coral gradient (`#F4748C` → `#C8485E`) and a white-highlight overlay. It is **unreferenced** anywhere in `app/` or `public/` (confirmed via grep) and is **not flagged** by `check:no-dusk-tokens` (coral hex values aren't in the forbidden-marker list, which targets named tokens/classes, not raw hex). Since it's dead and doesn't block the SC1 gate or the build, it was left in place rather than deleted — deleting unreferenced assets outside the plan's explicit `files_modified` list is out of this plan's scope. Candidate for cleanup in a later phase or a follow-up chore commit.
