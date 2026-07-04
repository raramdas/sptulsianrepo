#!/usr/bin/env python3
"""
tenant_manager.py — discovers active tenants from the shared ADMIN schema's
tenant_config table, decrypts their credentials, and opens per-tenant
connections. This is the entry point every multi-tenant bot run starts from.

A TenantContext bundles everything a bot needs for one tenant's run:
Kite credentials (decrypted), an open Oracle connection to their own schema,
and identifying info for logging.
"""
from dataclasses import dataclass
import oracledb

from config import log, ORACLE_DSN, ORACLE_WALLET_DIR, ORACLE_WALLET_PASSWORD, ORACLE_USER, ORACLE_PASSWORD
from crypto_utils import decrypt


@dataclass
class TenantContext:
    tenant_id: int
    tenant_name: str
    db_username: str
    kite_user_id: str
    kite_password: str
    kite_totp_secret: str
    whatsapp_number: str = None
    meta_phone_number_id: str = None
    meta_waba_id: str = None
    meta_access_token: str = None
    connection: object = None  # set by open_tenant_connection()


def get_admin_connection():
    """Connection to the SHARED control schema (tenant_config, recommendations,
    stock_cap_classification) — this is the existing ADMIN user."""
    return oracledb.connect(
        user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def get_active_tenants():
    """Fetch every active tenant's decrypted credentials (but do NOT open
    their Oracle connections yet — that happens lazily per tenant in the
    bot loop, so one tenant's DB issue doesn't block listing the others)."""
    conn = get_admin_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT tenant_id, tenant_name, db_username, db_password_enc,
                   kite_user_id, kite_password_enc, kite_totp_secret_enc,
                   whatsapp_number, meta_phone_number_id, meta_waba_id, meta_access_token_enc
            FROM tenant_config WHERE is_active = 'Y'
            ORDER BY tenant_id
        """)
        tenants = []
        for row in cur.fetchall():
            (tenant_id, tenant_name, db_username, db_password_enc,
             kite_user_id, kite_password_enc, kite_totp_secret_enc,
             whatsapp_number, meta_phone_number_id, meta_waba_id, meta_access_token_enc) = row
            tenants.append(TenantContext(
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                db_username=db_username,
                kite_user_id=kite_user_id,
                kite_password=decrypt(kite_password_enc),
                kite_totp_secret=decrypt(kite_totp_secret_enc),
                whatsapp_number=whatsapp_number,
                meta_phone_number_id=meta_phone_number_id,
                meta_waba_id=meta_waba_id,
                meta_access_token=decrypt(meta_access_token_enc) if meta_access_token_enc else None,
            ))
        return tenants, conn  # caller closes conn when done with lookups
    except Exception:
        conn.close()
        raise


def get_tenant_db_password(admin_conn, tenant_id):
    """Fetch and decrypt just this tenant's DB password (kept separate from
    get_active_tenants() so we don't hold every tenant's DB password in
    memory longer than needed — only decrypt it right before connecting)."""
    cur = admin_conn.cursor()
    cur.execute("SELECT db_password_enc FROM tenant_config WHERE tenant_id = :id", {'id': tenant_id})
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Tenant {tenant_id} not found")
    return decrypt(row[0])


def open_tenant_connection(admin_conn, tenant):
    """Decrypt this tenant's DB password and open a connection to THEIR
    own isolated schema. Returns the connection; caller is responsible
    for closing it."""
    db_password = get_tenant_db_password(admin_conn, tenant.tenant_id)
    return oracledb.connect(
        user=tenant.db_username, password=db_password, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def for_each_active_tenant(work_fn):
    """Run work_fn(tenant, tenant_conn) for every active tenant, in isolation —
    one tenant's exception is logged and skipped, never stops the others.
    Manages connection lifecycle (admin lookup conn + each tenant's own conn).
    """
    tenants, admin_conn = get_active_tenants()
    log(f"Found {len(tenants)} active tenant(s): {[t.tenant_name for t in tenants]}")

    results = {}
    try:
        for tenant in tenants:
            log(f"=== Tenant: {tenant.tenant_name} ({tenant.db_username}) ===")
            tenant_conn = None
            try:
                tenant_conn = open_tenant_connection(admin_conn, tenant)
                results[tenant.tenant_name] = work_fn(tenant, tenant_conn)
            except Exception as e:
                log(f"  ERROR for tenant {tenant.tenant_name}: {e}")
                results[tenant.tenant_name] = {'error': str(e)}
            finally:
                if tenant_conn:
                    tenant_conn.close()
    finally:
        admin_conn.close()

    return results


if __name__ == '__main__':
    tenants, conn = get_active_tenants()
    print(f"Active tenants ({len(tenants)}):")
    for t in tenants:
        print(f"  #{t.tenant_id} {t.tenant_name} -> schema {t.db_username}, kite_user={t.kite_user_id}")
    conn.close()
