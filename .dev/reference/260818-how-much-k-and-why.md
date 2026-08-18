# How much k, and the numbers behind the answer

**Written 2026-08-18, before the re-capture, because k is a spend decision and the guide does not
name a number.** `.dev/reference/260818-llm-eval-fundamentals.md` is the practice; this is that
practice applied to one question, with the arithmetic shown so nobody has to take it on trust.

## What the guide states

- **§2.** "A single run cannot tell these apart. Any success rate quoted from one pass per case is
  silent about which of the two failures it is looking at." That is the only hard floor it gives:
  **k > 1**.
- **§11.** "Never quote a success rate as a point estimate. **A rate over five trials moves double
  digits on the sixth.** Quote an interval, and treat a wide one as not shippable at any point
  estimate." So **five is the guide's own example of a rate that is still unstable.**

It names no k. Applying §11's rule is what produces one.

## The numbers

95% Wilson score interval on `reliable@k`, for a scenario that passes EVERY run:

```
   k   observed          interval    width  the strongest honest claim
   1     1/1          [0.21, 1.00]    0.79  none: pass@k and reliable@k are the same number
   3     3/3          [0.44, 1.00]    0.56  "at least 44% reliable"
   5     5/5          [0.57, 1.00]    0.43  "at least 57% reliable"
  10   10/10          [0.72, 1.00]    0.28  "at least 72% reliable"
  20   20/20          [0.84, 1.00]    0.16  "at least 84% reliable"
```

**And the comparison that decides it.** Can the corpus tell ALWAYS from NEVER?

```
  k=3    0/3 -> [0.00, 0.56]     3/3 -> [0.44, 1.00]     OVERLAP
  k=5    0/5 -> [0.00, 0.43]     5/5 -> [0.57, 1.00]     no overlap
```

**k=5 is the smallest k at which "never passes" and "always passes" have non-overlapping 95%
intervals.** k=3 cannot separate them, so it cannot support the diagnosis `8.1` exists for.

The three diagnoses at k=5, which is the whole point:

```
  0/5   reliable@k   0%   [0.00, 0.43]   CANNOT. Change the model, tools or architecture
  3/5   reliable@k  60%   [0.23, 0.88]   FLAKY. The work is variance
  5/5   reliable@k 100%   [0.57, 1.00]   consistent, to the limit k allows
```

## What k=5 does NOT buy

A shipping claim. M2's claim is consistency, and the strongest honest sentence at k=5 is "at least
57% reliable". A shipping number wants k >= 10, which is 200 live agent turns.

**The top-up is the way out of pre-buying that.** `capture_responses.py --runs K` tops a scenario up
rather than skipping it, so a scenario that comes back flaky at k=5 can be taken to k=25 for 20 more
turns, instead of re-capturing all twenty.

## Reproduce it

Stdlib only. This lives here rather than in `rates.py` because `8.2c` is the row that puts intervals
in the harness, and until it lands the harness prints point estimates that §11 says never to quote.

```python
import math

def wilson(x, n, z=1.96):
    """95% score interval for x successes in n trials."""
    if n == 0:
        return None
    p = x / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)

for k in (1, 3, 5, 10, 20):
    print(k, wilson(k, k))
for x, n in ((0, 5), (3, 5), (5, 5), (0, 3), (3, 3)):
    print(f"{x}/{n}", wilson(x, n))
```

## The consequence for `8.2`

The harness has **no interval, no chance correction, and no confusion matrix**: `grep -rniE
"kappa|matthews" app tests` returns nothing that concerns judge calibration. So every number the
corpus produces at any k is a point estimate, which §11 forbids quoting. `8.2c` is not cosmetic; it
is what makes a k adequate or inadequate visible instead of arguable.
