"""NaN must never reach an Oracle NUMBER column.

A missing target arrives from pandas as NaN, not None. Every intuitive guard
fails open on NaN — `not nan` is False, `nan <= 0` is False — so it passed
straight through and failed the whole INSERT with DPY-4004 on 2026-09-08.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
from lib import conviction_lite as cl
import main_conviction as mc

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

NAN = float('nan')

print("=== why the obvious guard fails ===")
ok(not (not NAN), "`not nan` is False — NaN looks truthy", not NAN)
ok(not (NAN <= 0), "`nan <= 0` is False — NaN looks positive", NAN <= 0)
print("  so `if not x or x <= 0` lets NaN through entirely")

print("\n=== _positive rejects everything unusable ===")
for v, want in [(NAN, False), (np.float64('nan'), False), (float('inf'), False),
                (float('-inf'), False), (None, False), (0, False), (-1, False),
                ('abc', False), (1.5, True), (np.float64(2.5), True)]:
    got = cl._positive(v)
    ok(got == want, f"_positive({v!r}) -> {got}", f"wanted {want}")

print("\n=== reach_z never returns NaN ===")
for tgt in (NAN, np.float64('nan'), None, 0, -5):
    ok(cl.reach_z(100.0, tgt, 0.02) is None, f"target={tgt!r} -> None")
for vol in (NAN, None, 0, -0.01):
    ok(cl.reach_z(100.0, 110.0, vol) is None, f"vol={vol!r} -> None")
z = cl.reach_z(100.0, 106.0, 0.02)
ok(z is not None and z == z, "a valid case still returns a real number", z)

print("\n=== _num_or_none sanitises every NUMBER bind ===")
for v, want in [(NAN, None), (np.float64('nan'), None), (float('inf'), None),
                (None, None), ('x', None), (1.5, 1.5), (np.float64(2.0), 2.0), (3, 3.0)]:
    got = mc._num_or_none(v)
    ok(got == want or (got is None and want is None),
       f"_num_or_none({v!r}) -> {got!r}", f"wanted {want!r}")

print("\n=== the real 2026-09-08 shape: NaN target from pandas ===")
import pandas as pd
row = pd.Series({'trade_id': 704, 'symbol': 'CDSL', 'stock_name': 'CDSL',
                 'category_name': 'Big Gems', 'target_price': np.float64('nan')})
ok(row['target_price'] is not None, "NaN is not None, so `is not None` passes")
ok(mc._num_or_none(row['target_price']) is None,
   "_num_or_none turns it into None, which is what the guards expect")

print(f"\n{'='*54}\n  {P} passed, {F} failed\n{'='*54}")
sys.exit(1 if F else 0)
