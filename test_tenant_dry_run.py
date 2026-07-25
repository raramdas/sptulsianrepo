#!/usr/bin/env python3
"""
test_tenant_dry_run.py — safe, end-to-end verification of a newly
provisioned tenant, WITHOUT placing any real Kite order and WITHOUT
depending on config.py's global DRY_RUN flag (which controls your existing
live cron jobs and should not be touched by this test).

What this checks, in order:
  1. Tenant record exists and decrypts correctly
  2. Real Kite login works using the TENANT's own credentials (read-only —
     logging in does not place any order)
  3. Connecting to the tenant's own Oracle schema works
  4. The tenant's budget views compute correctly
  5. A real market price can be fetched via Kite (read-only)
  6. A trade row can be written to the tenant's OWN trades table, tagged
     DRY_RUN, with buy_order_id='DRY_RUN' — proving the Oracle write path
     works without ever calling kite_buy() or place_gtt()

Usage:
    python3 test_tenant_dry_run.py "<Tenant Name>"
"""
import sys
import os
import oracledb
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

from crypto_utils import decrypt
from kite_client import get_enctoken_for, resolve_kite_symbol, get_market_price

ORACLE_DSN             = os.environ['ORACLE_DSN']
ORACLE_WALLET_DIR      = os.environ['ORACLE_WALLET_DIR']
ORACLE_WALLET_PASSWORD = os.environ['ORACLE_WALLET_PASSWORD']
ADMIN_USER             = os.environ['ORACLE_USER']
ADMIN_PASSWORD         = os.environ['ORACLE_PASSWORD']


def get_admin_connection():
    return oracledb.connect(
        user=ADMIN_USER, password=ADMIN_PASSWORD, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_tenant_dry_run.py \"<Tenant Name>\"")
        sys.exit(1)
    tenant_name = sys.argv[1]

    print(f"=== Dry run verification for tenant '{tenant_name}' ===\n")

    # ── 1. Look up and decrypt the tenant record ──────────────────
    admin_conn = get_admin_connection()
    cur = admin_conn.cursor()
    cur.execute("""
        SELECT db_username, db_password_enc, kite_user_id, kite_password_enc, kite_totp_secret_enc
        FROM tenant_config WHERE tenant_name = :name AND is_active = 'Y'
    """, {'name': tenant_name})
    row = cur.fetchone()
    admin_conn.close()
    if not row:
        print(f"Tenant '{tenant_name}' not found or inactive.")
        sys.exit(1)

    db_username, db_password_enc, kite_user_id, kite_password_enc, kite_totp_secret_enc = row
    db_password = decrypt(db_password_enc)
    kite_password = decrypt(kite_password_enc)
    kite_totp_secret = decrypt(kite_totp_secret_enc)
    print(f"1. Tenant record found. DB schema: {db_username}")

    # ── 2. Real Kite login test (read-only, no orders placed) ──────
    print("2. Testing Kite login with this tenant's own credentials...")
    enctoken = None
    try:
        enctoken = get_enctoken_for(kite_user_id, kite_password, kite_totp_secret)
        print(f"   Kite login OK — enctoken obtained (length {len(enctoken)}).")
    except Exception as e:
        print(f"   Kite login FAILED: {e}")
        print("   (Continuing with remaining checks that don't need Kite.)")

    # ── 3. Connect to the tenant's own isolated Oracle schema ──────
    print("3. Connecting to tenant's own Oracle schema...")
    tenant_conn = oracledb.connect(
        user=db_username, password=db_password, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )
    tcur = tenant_conn.cursor()
    print("   Connected OK.")

    # ── 4. Budget views ──────────────────────────────────────────
    tcur.execute("""
        SELECT category_name, category_budget, invested, available
        FROM category_budget_status ORDER BY category_name
    """)
    print("\n4. Current budget status (tenant's own isolated data):")
    for r in tcur.fetchall():
        print(f"   {r[0]}: budget=Rs.{r[1]:,.2f} invested=Rs.{r[2]:,.2f} available=Rs.{r[3]:,.2f}")

    # ── 5. Real market price fetch (read-only) ──────────────────
    if enctoken:
        print("\n5. Testing a real market price fetch (RELIANCE)...")
        symbol = resolve_kite_symbol('Reliance Industries', enctoken)
        price = get_market_price('Reliance Industries', enctoken)
        print(f"   Symbol resolved: {symbol}, live price: {price}")
    else:
        print("\n5. Skipped (Kite login failed above).")

    # ── 6. Write a DRY_RUN trade directly — no Kite order involved ──
    print("\n6. Writing a test DRY_RUN trade into the tenant's own trades table...")
    tcur.execute("SELECT category_id FROM category_allocation WHERE category_name = 'Big Gems'")
    category_id_row = tcur.fetchone()
    if not category_id_row:
        print("   Could not find 'Big Gems' category — skipping write test.")
    else:
        category_id = category_id_row[0]
        tcur.execute("""
            INSERT INTO trades (category_id, category_name, stock_name, symbol, stock_type,
                buy_date, status, my_buy_price, my_buy_qty, invested_amount,
                order_type, buy_order_id, notes)
            VALUES (:cid, 'Big Gems', 'TEST STOCK', 'TEST', 'Large Cap',
                SYSDATE, 'DRY_RUN', 100, 1, 100, 'LIMIT', 'DRY_RUN',
                'Inserted by test_tenant_dry_run.py verification')
        """, {'cid': category_id})
        tenant_conn.commit()
        tcur.execute("SELECT COUNT(*) FROM trades")
        print(f"   Test trade inserted. Tenant's trades table now has {tcur.fetchone()[0]} row(s).")

    tenant_conn.close()
    print(f"\n=== Dry run verification for '{tenant_name}' complete ===")
    print("No Kite orders were placed at any point in this script.")


if __name__ == '__main__':
    main()
