#!/usr/bin/env python3
"""
setup_shared_schema.py

Creates the SHARED "control" schema tables — these live in the existing ADMIN
schema (no need for a separate control user; ADMIN already is the natural
home for cross-tenant data).

Tables created here:
  - tenant_config           : one row per tenant, ALL secrets encrypted
  - recommendations         : the single shared advisory feed everyone reads

NOT created here (already exists from v4, untouched):
  - stock_cap_classification : shared AMFI reference data, no changes needed

Run once. Safe to re-run (drops and recreates tenant_config/recommendations only
— never touches stock_cap_classification, trades, or any per-tenant schema).
"""
import os
import oracledb
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

ORACLE_USER            = os.environ['ORACLE_USER']
ORACLE_PASSWORD        = os.environ['ORACLE_PASSWORD']
ORACLE_DSN             = os.environ['ORACLE_DSN']
ORACLE_WALLET_DIR      = os.environ['ORACLE_WALLET_DIR']
ORACLE_WALLET_PASSWORD = os.environ['ORACLE_WALLET_PASSWORD']

DDL_STATEMENTS = [
    "DROP TABLE recommendations PURGE",
    "DROP TABLE tenant_config PURGE",

    # ── tenant_config: the registry. Every secret field is Fernet-encrypted
    # at the application layer before insert — this table never sees plaintext.
    # The master Fernet key lives only in .env on the VM, never in the DB.
    """
    CREATE TABLE tenant_config (
        tenant_id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_name            VARCHAR2(80) NOT NULL,

        -- This tenant's OWN Oracle DB login, for their isolated schema
        db_username             VARCHAR2(40)  NOT NULL UNIQUE,
        db_password_enc         VARCHAR2(400) NOT NULL,

        -- Kite / Zerodha (encrypted)
        kite_user_id            VARCHAR2(40),
        kite_password_enc       VARCHAR2(400),
        kite_totp_secret_enc    VARCHAR2(400),
        enctoken_enc            VARCHAR2(1000),   -- refreshed daily via TOTP login
        enctoken_updated_at     TIMESTAMP,

        -- WhatsApp (Meta Cloud API), encrypted
        whatsapp_number         VARCHAR2(20),
        meta_phone_number_id    VARCHAR2(40),
        meta_waba_id            VARCHAR2(40),
        meta_access_token_enc   VARCHAR2(600),

        -- lifecycle
        is_active               CHAR(1) DEFAULT 'Y' CHECK (is_active IN ('Y','N')),
        created_at              TIMESTAMP DEFAULT SYSTIMESTAMP,
        updated_at              TIMESTAMP DEFAULT SYSTIMESTAMP
    )
    """,

    # ── recommendations: shared advisory feed, one row per (symbol, email_date)
    """
    CREATE TABLE recommendations (
        rec_id             NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        stock_name         VARCHAR2(120),
        symbol             VARCHAR2(40),
        stock_type         VARCHAR2(20),      -- Large/Mid/Small/Micro Cap, from AMFI lookup
        category_name      VARCHAR2(60),      -- suggested category, from email subject
        recommended_price  NUMBER(12,2),
        email_date         DATE,
        email_subject      VARCHAR2(300),

        -- Manual fields, set by a human on the Recommendations screen before 09:30
        target_price       NUMBER(12,2),
        timeframe          VARCHAR2(40),
        have_interest      VARCHAR2(20),      -- 'Have Interest' | 'No Interest' | NULL (pending)

        status              VARCHAR2(20) DEFAULT 'Pending',  -- Pending|Approved|Executed|Skipped
        created_at          TIMESTAMP DEFAULT SYSTIMESTAMP,
        updated_at          TIMESTAMP DEFAULT SYSTIMESTAMP,
        CONSTRAINT uq_rec UNIQUE (symbol, email_date)
    )
    """,

    "CREATE INDEX idx_rec_status ON recommendations(status)",
    "CREATE INDEX idx_rec_email_date ON recommendations(email_date)",
    "CREATE INDEX idx_tenant_active ON tenant_config(is_active)",
]


def get_connection():
    return oracledb.connect(
        user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR, wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def run():
    conn = get_connection()
    cursor = conn.cursor()
    print("Connected to ADMIN (shared/control) schema.")

    for stmt in DDL_STATEMENTS:
        try:
            cursor.execute(stmt)
            print(f"OK: {stmt.strip().splitlines()[0][:70]}")
        except oracledb.DatabaseError as e:
            error_obj, = e.args
            if "ORA-00942" in str(error_obj) and stmt.strip().startswith("DROP"):
                print(f"SKIP (doesn't exist yet): {stmt.strip()[:70]}")
            else:
                print(f"ERROR on: {stmt.strip()[:80]}\n  {error_obj}")
                raise

    conn.commit()
    cursor.close()
    conn.close()
    print("\n=== Shared/control schema setup complete ===")
    print("Tables: tenant_config, recommendations")
    print("(stock_cap_classification already exists from v4, untouched)")


if __name__ == '__main__':
    run()
