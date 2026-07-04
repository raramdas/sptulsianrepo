#!/usr/bin/env python3
"""
provision_tenant.py — creates a brand new tenant end-to-end:

  1. Generates a random Oracle DB username + strong password for this tenant
  2. Creates that Oracle user (their own isolated schema)
  3. Grants them read-only access to the shared recommendations +
     stock_cap_classification tables
  4. Creates their trades / portfolio_budget / category_allocation tables
     and the two budget views, inside their new schema
  5. Encrypts all secrets (Kite login, TOTP secret, their new DB password,
     WhatsApp/Meta token) and inserts one row into ADMIN.tenant_config
  6. Seeds their portfolio_budget + category_allocation with sensible
     defaults (same 6-category SPTulsian structure as v4) — edit afterwards
     via the dashboard's Settings page

If anything fails partway, it attempts to clean up (DROP USER ... CASCADE)
rather than leaving an orphaned half-built schema behind.

Usage:
    python3 provision_tenant.py
    (interactive prompts — see main() below)
"""
import os
import re
import secrets
import string
import oracledb
from dotenv import load_dotenv

from crypto_utils import encrypt
from tenant_schema_template import tenant_ddl_statements, tenant_view_statements, grant_statements

load_dotenv('/home/ubuntu/.env')

ORACLE_USER            = os.environ['ORACLE_USER']
ORACLE_PASSWORD        = os.environ['ORACLE_PASSWORD']
ORACLE_DSN             = os.environ['ORACLE_DSN']
ORACLE_WALLET_DIR      = os.environ['ORACLE_WALLET_DIR']
ORACLE_WALLET_PASSWORD = os.environ['ORACLE_WALLET_PASSWORD']

# Default category allocations, matching v4's proven structure —
# edit per-tenant afterwards via the dashboard
DEFAULT_TOTAL_BUDGET = 200000.00
DEFAULT_CATEGORIES = [
    # name, allocation_pct, large_pct, mid_pct, small_pct, micro_pct
    ('Little Gems',              20, 10, 6, 4, 2),
    ('Big Gems',                 20, 10, 6, 4, 2),
    ('Short Term Investments',   15, 10, 6, 4, 2),
    ('Medium Term Investments',  20, 10, 6, 4, 2),
    ('Regular Income Bluechips', 20, 10, 6, 4, 2),
    ('Multibagger Stocks',       10,  6, 4, 2, 2),
]


def get_admin_connection():
    return oracledb.connect(
        user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def slugify_username(tenant_name):
    """Turn 'Rajesh Ramdas' into a valid Oracle identifier 'TENANT_RAJESH_RAMDAS'."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', tenant_name.strip()).strip('_').upper()
    return f"TENANT_{slug}"[:30]  # Oracle identifier length limit


def generate_db_password():
    """A strong password that satisfies Oracle's default complexity rules:
    starts with a letter, mixes upper/lower/digit, no ambiguous quote chars."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = ''.join(secrets.choice(alphabet) for _ in range(24))
        if (pw[0].isalpha() and any(c.isupper() for c in pw)
                and any(c.islower() for c in pw) and any(c.isdigit() for c in pw)):
            return pw


def create_tenant_schema(cursor, db_username, db_password):
    """Steps 2-4: create the Oracle user and their full schema."""
    print(f"Creating Oracle user {db_username}...")
    cursor.execute(f'CREATE USER {db_username} IDENTIFIED BY "{db_password}"')
    cursor.execute(f"GRANT CONNECT, RESOURCE TO {db_username}")
    cursor.execute(f"ALTER USER {db_username} QUOTA UNLIMITED ON DATA")
    print("  User created, granted CONNECT/RESOURCE + DATA quota.")

    print("Granting read access to shared reference tables...")
    for stmt in grant_statements(db_username, ORACLE_USER):
        cursor.execute(stmt)
    print("  Granted SELECT + created synonyms for recommendations + stock_cap_classification.")

    print("Creating tenant tables...")
    for stmt in tenant_ddl_statements(db_username):
        try:
            cursor.execute(stmt)
        except oracledb.DatabaseError as e:
            error_obj, = e.args
            if "ORA-00942" in str(error_obj) and stmt.strip().upper().startswith("DROP"):
                continue  # table doesn't exist yet on first run, expected
            raise
    print("  Tables created: portfolio_budget, category_allocation, trades.")

    print("Creating budget views...")
    for stmt in tenant_view_statements(db_username):
        cursor.execute(stmt)
    print("  Views created: category_budget_status, stock_type_budget_status.")


def seed_default_budget(db_username, db_password, total_budget):
    """Connect AS the new tenant to seed their own budget/category rows
    (keeps ownership of this data cleanly with the tenant, not ADMIN)."""
    print(f"Seeding default budget (Rs.{total_budget:,.2f}) and categories...")
    tenant_conn = oracledb.connect(
        user=db_username, password=db_password, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )
    try:
        cur = tenant_conn.cursor()
        budget_id_var = cur.var(int)
        cur.execute(
            "INSERT INTO portfolio_budget (total_budget, is_active) VALUES (:tb, 'Y') "
            "RETURNING budget_id INTO :bid",
            {'tb': total_budget, 'bid': budget_id_var}
        )
        budget_id = budget_id_var.getvalue()[0]
        for name, ap, lp, mp, sp, mcp in DEFAULT_CATEGORIES:
            cur.execute("""
                INSERT INTO category_allocation
                    (budget_id, category_name, allocation_pct, large_cap_pct, mid_cap_pct, small_cap_pct, micro_cap_pct)
                VALUES (:bid, :name, :ap, :lp, :mp, :sp, :mcp)
            """, {'bid': budget_id, 'name': name, 'ap': ap, 'lp': lp, 'mp': mp, 'sp': sp, 'mcp': mcp})
        tenant_conn.commit()
        print(f"  Seeded {len(DEFAULT_CATEGORIES)} categories.")
    finally:
        tenant_conn.close()


def register_tenant(cursor, tenant_name, db_username, db_password,
                     kite_user_id, kite_password, kite_totp_secret,
                     whatsapp_number, meta_phone_number_id, meta_waba_id, meta_access_token):
    """Step 5: encrypted row in ADMIN.tenant_config."""
    print("Registering tenant in tenant_config (all secrets encrypted)...")
    cursor.execute("""
        INSERT INTO tenant_config (
            tenant_name, db_username, db_password_enc,
            kite_user_id, kite_password_enc, kite_totp_secret_enc,
            whatsapp_number, meta_phone_number_id, meta_waba_id, meta_access_token_enc,
            is_active
        ) VALUES (
            :tenant_name, :db_username, :db_password_enc,
            :kite_user_id, :kite_password_enc, :kite_totp_secret_enc,
            :whatsapp_number, :meta_phone_number_id, :meta_waba_id, :meta_access_token_enc,
            'Y'
        )
    """, {
        'tenant_name': tenant_name,
        'db_username': db_username,
        'db_password_enc': encrypt(db_password),
        'kite_user_id': kite_user_id,
        'kite_password_enc': encrypt(kite_password),
        'kite_totp_secret_enc': encrypt(kite_totp_secret),
        'whatsapp_number': whatsapp_number,
        'meta_phone_number_id': meta_phone_number_id,
        'meta_waba_id': meta_waba_id,
        'meta_access_token_enc': encrypt(meta_access_token),
    })
    print("  tenant_config row inserted.")


def provision(tenant_name, kite_user_id, kite_password, kite_totp_secret,
              whatsapp_number=None, meta_phone_number_id=None, meta_waba_id=None,
              meta_access_token=None, total_budget=DEFAULT_TOTAL_BUDGET):
    db_username = slugify_username(tenant_name)
    db_password = generate_db_password()

    admin_conn = get_admin_connection()
    cursor = admin_conn.cursor()

    try:
        create_tenant_schema(cursor, db_username, db_password)
        admin_conn.commit()  # commit schema creation before connecting as the new user

        seed_default_budget(db_username, db_password, total_budget)

        register_tenant(cursor, tenant_name, db_username, db_password,
                         kite_user_id, kite_password, kite_totp_secret,
                         whatsapp_number, meta_phone_number_id, meta_waba_id, meta_access_token)
        admin_conn.commit()

        print(f"\n=== Tenant '{tenant_name}' provisioned successfully ===")
        print(f"  DB username: {db_username}")
        print(f"  (DB password stored encrypted in tenant_config, not shown here)")
        return db_username

    except Exception as e:
        print(f"\nERROR during provisioning: {e}")
        print(f"Attempting cleanup - dropping user {db_username} if it was created...")
        try:
            admin_conn.rollback()
            cleanup_cursor = admin_conn.cursor()
            cleanup_cursor.execute(f"DROP USER {db_username} CASCADE")
            admin_conn.commit()
            print(f"  Cleaned up: {db_username} dropped.")
        except Exception as cleanup_error:
            print(f"  Cleanup also failed (user may not have been created yet): {cleanup_error}")
        raise

    finally:
        cursor.close()
        admin_conn.close()


def main():
    print("=== New Tenant Provisioning ===\n")
    tenant_name = input("Tenant name (e.g. 'Rajesh Ramdas'): ").strip()
    kite_user_id = input("Kite user ID: ").strip()
    kite_password = input("Kite password: ").strip()
    kite_totp_secret = input("Kite TOTP secret: ").strip()
    whatsapp_number = input("WhatsApp number (optional, press Enter to skip): ").strip() or None
    meta_phone_number_id = input("Meta phone number ID (optional): ").strip() or None
    meta_waba_id = input("Meta WABA ID (optional): ").strip() or None
    meta_access_token = input("Meta access token (optional): ").strip() or None

    budget_input = input(f"Total budget (Rs., default {DEFAULT_TOTAL_BUDGET:,.0f}): ").strip()
    total_budget = float(budget_input) if budget_input else DEFAULT_TOTAL_BUDGET

    confirm = input(f"\nCreate tenant '{tenant_name}' with budget Rs.{total_budget:,.2f}? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return

    provision(
        tenant_name=tenant_name,
        kite_user_id=kite_user_id,
        kite_password=kite_password,
        kite_totp_secret=kite_totp_secret,
        whatsapp_number=whatsapp_number,
        meta_phone_number_id=meta_phone_number_id,
        meta_waba_id=meta_waba_id,
        meta_access_token=meta_access_token,
        total_budget=total_budget,
    )


if __name__ == '__main__':
    main()
