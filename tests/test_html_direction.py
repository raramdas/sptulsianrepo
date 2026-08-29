"""HTML sections must report Buy/Sell direction. Offline — synthetic markup."""
import sys
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
from bs4 import BeautifulSoup
from lib import spt_scraper as s

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

def parse(cells_html, source='active'):
    html = f"<table><tr>{''.join(f'<td>{c}</td>' for c in cells_html)}</tr></table>"
    table = BeautifulSoup(html, 'html.parser').find('table')
    rows = s._parse_html_table(table, source)
    return rows[0] if rows else None

# Real shapes, taken from the live portal.
SHORT_TERM = ['CDSL', '', '', 'Buy', '1,543 (05th Dec @ 12:02 pm)',
              '1,633 (30th Sep)', 'Have interest',
              'CDSL Disclosure: Have interest',
              'Buy @ 1,543 (05th Dec @ 12:02 pm)', 'Target: 1,633 (30th Sep)']

MEDIUM_TERM = ['Happy Forgings', '', '', '2,251 (28th Aug @ 09:10 am)',
               '2,589 (8-10 months)',
               'High-growth player moving to complex segment including Data centre, '
               'Encouraging guidance, Valuation still attractive for medium term holding',
               'No interest', 'Read More',
               'Happy Forgings Disclosure: No interest',
               'Buy @ 2,251 (28th Aug @ 09:10 am) Target: 2,589 (8-10 months)']

MULTIBAGGER = ['Mangal Electrical', '', '', 'Buy', '262 (25th Aug 2026 @ 10:22 am)',
               '314 (20th Aug 2027)', 'Have interest',
               'Mangal Electrical Disclosure: Have interest',
               'Buy @ 262 (25th Aug 2026 @ 10:22 am)', 'Target: 314 (20th Aug 2027)']

print("=== every HTML section now reports direction ===")
for name, cells in (('Short Term', SHORT_TERM),
                    ('Medium Term', MEDIUM_TERM),
                    ('Multibagger', MULTIBAGGER)):
    r = parse(cells)
    print(f"  {name:<13} direction={r['direction']!r:<8} "
          f"buy={r['buy_price']} target={r['target_price']}")
    ok(r['direction'] == 'Buy', f"{name}: direction read")
    ok(r['buy_price'] is not None, f"{name}: price still parses", r['buy_price'])
    ok(r['target_price'] is not None, f"{name}: target still parses", r['target_price'])

print("\n=== a SELL row is read as a sell, with its price ===")
sell = ['CDSL', '', '', 'Sell', '1,543 (05th Dec @ 12:02 pm)',
        '1,633 (30th Sep)', 'Have interest', 'CDSL Disclosure: Have interest',
        'Sell @ 1,543 (05th Dec @ 12:02 pm)', 'Target: 1,633 (30th Sep)']
r = parse(sell)
print(f"  direction={r['direction']!r} buy_price={r['buy_price']}")
ok(r['direction'] == 'Sell', "Sell detected", r['direction'])
ok(r['buy_price'] == 1543.0, "price parses on a Sell row (old regex returned None)",
   r['buy_price'])

print("\n=== direction only in the mobile cell (no standalone cell) ===")
r = parse(MEDIUM_TERM)
ok(r['direction'] == 'Buy', "Medium Term has no standalone cell, still resolves")

sell_mt = list(MEDIUM_TERM)
sell_mt[-1] = 'Sell @ 2,251 (28th Aug @ 09:10 am) Target: 2,589 (8-10 months)'
sell_mt[3] = '2,251 (28th Aug @ 09:10 am)'
r = parse(sell_mt)
ok(r['direction'] == 'Sell', "Medium Term Sell detected from mobile cell", r['direction'])

print("\n=== an archived row's exit remark must not become the direction ===")
archived = ['CDSL', '', '', 'Buy', '1,343 (03rd Aug @ 10:00 am)',
            'Target: 1,426 (30th Sep)', 'Have interest',
            'Buy @ 1,343 (03rd Aug @ 10:00 am)', 'Target met, sold @ 1,426']
r = parse(archived, source='archive')
print(f"  direction={r['direction']!r} buy_price={r['buy_price']} closed={r['closed']}")
ok(r['direction'] == 'Buy', "still Buy, not confused by the exit", r['direction'])
ok(r['buy_price'] == 1343.0, "entry price, not the exit price", r['buy_price'])
ok(r['closed'] is True, "recognised as closed")

exit_sell = ['CDSL', '', '', 'Buy', '1,343 (03rd Aug @ 10:00 am)',
             'Target: 1,426 (30th Sep)', 'Have interest',
             'Buy @ 1,343 (03rd Aug @ 10:00 am)', 'Exited: Sell @ 1,426 booked']
r = parse(exit_sell, source='archive')
print(f"  exit-says-Sell row -> direction={r['direction']!r} buy_price={r['buy_price']}")
ok(r['direction'] == 'Buy',
   "an exit remark reading 'Sell @' does NOT flip the call's direction", r['direction'])
ok(r['buy_price'] == 1343.0, "and does not become the entry price", r['buy_price'])

print("\n=== no direction stated -> blank, never a guessed 'Buy' ===")
bare = ['SOMESTOCK', '', '', '100 (01st Jan @ 09:00 am)',
        'Have interest', 'Target: 110 (3 Months)']
r = parse(bare)
print(f"  direction={r['direction']!r}")
ok(r['direction'] == '', "blank when the row does not say", r['direction'])

print(f"\n{'='*56}\n  {P} passed, {F} failed\n{'='*56}")
sys.exit(1 if F else 0)
