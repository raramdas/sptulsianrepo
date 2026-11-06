"""Export a consistent research snapshot. No DDL, DML, broker, or email imports."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path


# Deliberate allowlist: never export TENANT_CONFIG or credentials.
TABLES = {
    'TRADES': ('trade_id', '''rec_id category_name stock_name symbol stock_type buy_date
        recommended_price target_price timeframe have_interest status target_met
        target_met_date gain my_buy_date order_type buy_order_id market_price_at_buy
        my_buy_price my_buy_qty invested_amount my_sell_date my_sell_price my_sell_qty
        my_gain_loss gtt_id gtt_status created_at updated_at spt_direction
        spt_market_price_at_call spt_below_reco spt_rationale'''.split()),
    'RECOMMENDATIONS': ('rec_id', '''stock_name symbol stock_type category_name
        recommended_price email_date target_price timeframe have_interest status
        created_at updated_at'''.split()),
    'CONVICTION_SCORES': ('score_id', '''trade_id symbol stock_name category_name
        scored_at score evidence_pct tier verdict sector reasons warnings layers_json
        model reach_z'''.split()),
    'KITE_HOLDINGS_SNAPSHOT': ('tradingsymbol', '''account_label quantity average_price
        last_price pnl synced_at'''.split()),
    'KITE_ORDERS_SNAPSHOT': ('order_id', '''account_label order_timestamp tradingsymbol
        transaction_type order_type quantity filled_quantity price average_price status synced_at'''.split()),
    'KITE_GTT_SNAPSHOT': ('gtt_id', '''account_label symbol status trigger_price last_price
        quantity sell_price created_at expires_at synced_at'''.split()),
}


def extract(conn):
    cur = conn.cursor()
    cur.execute('SET TRANSACTION READ ONLY')
    try:
        cur.execute('SELECT SYSTIMESTAMP FROM dual')
        captured = cur.fetchone()[0].isoformat()
        literals = ','.join("'" + name + "'" for name in TABLES)
        cur.execute("SELECT table_name, column_name, data_type FROM user_tab_columns "
                    f"WHERE table_name IN ({literals}) ORDER BY table_name, column_id")
        schema = cur.fetchall()
        result = {'format_version': 1, 'captured_at': captured,
                  'source': 'oracle_read_only', 'schema': schema, 'tables': {}, 'missing': []}
        for table, (pk, optional) in TABLES.items():
            available = {r[1].lower() for r in schema if r[0] == table}
            if pk not in available:
                result['missing'].append(table)
                continue
            cols = [pk] + [x for x in optional if x in available]
            # Identifiers originate exclusively in the static allowlist above.
            cur.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY {pk}")
            # Materialize CLOBs while the connection is still open.
            result['tables'][table.lower()] = [
                dict(zip(cols, [v.read() if hasattr(v, 'read') else v for v in row]))
                for row in cur]
        if 'trades' not in result['tables']:
            raise ValueError('TRADES is unavailable in the connected schema; no snapshot produced')
        return result
    finally:
        conn.rollback()
        cur.close()


def serialize(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if hasattr(value, 'read'):
        return value.read()
    raise TypeError(type(value).__name__)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', default='/home/ubuntu/.env')
    p.add_argument('--out', required=True, help='New private snapshot JSON path (never overwritten)')
    args = p.parse_args()
    import oracledb
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    options = {k: os.environ[v] for k, v in
               [('user', 'ORACLE_USER'), ('password', 'ORACLE_PASSWORD'), ('dsn', 'ORACLE_DSN')]}
    if os.environ.get('ORACLE_WALLET_DIR'):
        options.update(config_dir=os.environ['ORACLE_WALLET_DIR'],
                       wallet_location=os.environ['ORACLE_WALLET_DIR'],
                       wallet_password=os.environ.get('ORACLE_WALLET_PASSWORD'))
    with oracledb.connect(**options) as conn:
        snapshot = extract(conn)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(snapshot, f, default=serialize, allow_nan=False, sort_keys=True)
    print(json.dumps({'snapshot': str(path), 'rows':
                     {k: len(v) for k, v in snapshot['tables'].items()},
                     'missing_tables': snapshot['missing']}))


if __name__ == '__main__':
    main()
