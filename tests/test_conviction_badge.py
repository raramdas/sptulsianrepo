"""The badge colour must agree with what the buy path does. No env needed."""
import sys, re
sys.path.insert(0, "/home/ubuntu/stockbot")
sys.path.insert(0, "/home/ubuntu/stockbot/dashboard")

import theme
from lib.bands import (CONVICTION_SIZING_ENABLED, CONVICTION_SIZING,
                       CONVICTION_MIN_SCORE, size_for)

P = F = 0
def ok(c, label, extra=''):
    global P, F
    if c: P += 1; print(f"  PASS  {label}")
    else: F += 1; print(f"  FAIL  {label}  {extra}")

def parse(score):
    html = theme.conviction_badge(score)
    colour = re.search(r'color:(#[0-9A-Fa-f]{6});', html)
    tip = re.search(r'title="([^"]*)"', html)
    return (colour.group(1) if colour else None), (tip.group(1) if tip else '')

NAME = {theme.POSITIVE: 'POSITIVE', theme.ACCENT: 'ACCENT',
        theme.WARNING: 'WARNING', theme.NEGATIVE: 'NEGATIVE'}

print(f"=== bands: enabled={CONVICTION_SIZING_ENABLED} {CONVICTION_SIZING} "
      f"floor={CONVICTION_MIN_SCORE} ===\n")
print(f"{'score':>6}  {'sizes to':>10}  {'colour':<9}  tooltip")
for v in (100, 90, 86, 85.0001, 85, 84, 70, 63, 62.9, 62.7, 50, 10.6, 6.7):
    c, tip = parse(v)
    amt = size_for(v)
    print(f"{v:>6}  {('Rs %s' % f'{amt:,}') if amt else 'not bought':>10}  "
          f"{NAME.get(c,c):<9}  {tip}")

print("\n=== the invariant: colour must agree with size_for() ===")
bad = []
for i in range(0, 1001):
    v = i / 10.0
    c, _ = parse(v)
    amt = size_for(v)
    if amt is None:
        expect = theme.NEGATIVE
    elif amt == CONVICTION_SIZING[0][1]:
        expect = theme.POSITIVE
    else:
        expect = theme.ACCENT
    if c != expect:
        bad.append((v, NAME.get(c, c), NAME.get(expect, expect), amt))
ok(not bad, f"all 1001 scores 0.0-100.0 coloured consistently with sizing",
   f"first mismatches: {bad[:5]}")

print("\n=== no colour spans the funded / not-funded boundary ===")
funded = {parse(i/10)[0] for i in range(0, 1001) if size_for(i/10) is not None}
unfunded = {parse(i/10)[0] for i in range(0, 1001) if size_for(i/10) is None}
overlap = funded & unfunded
ok(not overlap, "funded and not-bought share no colour",
   f"overlap: {[NAME.get(c,c) for c in overlap]}")

print("\n=== the two money bands are visually distinct ===")
top = {parse(i/10)[0] for i in range(0,1001) if size_for(i/10) == CONVICTION_SIZING[0][1]}
mid = {parse(i/10)[0] for i in range(0,1001) if size_for(i/10) == CONVICTION_SIZING[-1][1]}
ok(not (top & mid), "Rs 25,000 and Rs 10,000 use different colours",
   f"{[NAME.get(c,c) for c in top]} vs {[NAME.get(c,c) for c in mid]}")

print("\n=== boundary exactness (must match the buy path, not approximate it) ===")
for v, why in ((CONVICTION_MIN_SCORE, "floor exactly -> funded"),
               (CONVICTION_MIN_SCORE - 0.1, "just under floor -> not funded"),
               (CONVICTION_SIZING[0][0], "85 exactly -> mid band"),
               (CONVICTION_SIZING[0][0] + 0.1, "just over 85 -> top band")):
    c, tip = parse(v)
    amt = size_for(v)
    expect = (theme.NEGATIVE if amt is None else
              theme.POSITIVE if amt == CONVICTION_SIZING[0][1] else theme.ACCENT)
    ok(c == expect, f"{v}: {why}", f"got {NAME.get(c,c)}")

print("\n=== unscored stays blank, never a low score ===")
for v in (None, float('nan')):
    html = theme.conviction_badge(v)
    ok('—' in html and theme.NEGATIVE not in html, f"{v!r} renders as blank dash", html[:70])

print(f"\n{'='*52}\n  {P} passed, {F} failed\n{'='*52}")
sys.exit(1 if F else 0)
