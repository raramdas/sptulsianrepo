"""Cache adjusted prices with explicit, reviewed instrument mappings and sessions.

Does not infer exchange from a name or silently discard failed/delisted symbols.
The offline evidence runner never downloads or refreshes this cache implicitly.
"""
import argparse
import datetime as dt
import json
from pathlib import Path


def fetch(mapping, calendar, start, end):
    import yfinance as yf
    from research.evidence import number
    if calendar.get('verified') is not True or not calendar.get('source'):
        raise ValueError('Supply an independently verified trading-session calendar')
    result = {'basis': 'split_dividend_adjusted_close', 'source': 'Yahoo Finance via yfinance',
              'provider_version': yf.__version__, 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'calendar_source': calendar['source'], 'calendar_verified': True,
              'sessions': calendar['sessions'], 'series': {}, 'errors': {},
              'requested_start': start, 'requested_end_exclusive': end}
    for symbol, item in sorted(mapping.items()):
        if item.get('identity_verified') is not True or not item.get('provider_symbol'):
            result['errors'][symbol] = 'UNVERIFIED_INSTRUMENT_MAPPING'
            continue
        data = {**item, 'bars': []}
        result['series'][symbol] = data
        try:
            frame = yf.Ticker(item['provider_symbol']).history(
                start=start, end=end, interval='1d', auto_adjust=False,
                back_adjust=False, actions=True, repair=False, keepna=True,
                raise_errors=True, timeout=20)
            if frame.empty or 'Adj Close' not in frame:
                result['errors'][symbol] = 'MISSING_ADJUSTED_CLOSE'
                continue
            data['bars'] = [{'date': index.date().isoformat(),
                             'adjusted_close': number(row['Adj Close']),
                             'provider_close': number(row.get('Close')),
                             'dividend': number(row.get('Dividends')),
                             'split': number(row.get('Stock Splits'))}
                            for index, row in frame.iterrows()]
        except Exception as exc:
            result['errors'][symbol] = type(exc).__name__
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mapping', required=True)
    p.add_argument('--calendar', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True, help='Exclusive date')
    p.add_argument('--out', required=True)
    p.add_argument('--exclusions', help='Reviewed recommendation-id to reason JSON; retained in price provenance')
    a = p.parse_args()
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    result = fetch(json.loads(Path(a.mapping).read_text()), json.loads(Path(a.calendar).read_text()), a.start, a.end)
    if a.exclusions:
        result['excluded_recommendations'] = json.loads(Path(a.exclusions).read_text())
    with open(a.out, 'x') as f:
        json.dump(result, f, sort_keys=True, allow_nan=False)
    print(json.dumps({'series': len(result['series']), 'errors': result['errors']}))


if __name__ == '__main__':
    main()
