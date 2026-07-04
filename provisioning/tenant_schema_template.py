#!/usr/bin/env python3
"""
tenant_schema_template.py

Defines the DDL for a SINGLE TENANT's isolated schema. This is not a
standalone script to run directly — it's imported by the (not-yet-built)
provision_tenant.py, which will:
  1. CREATE USER <tenant_db_user> IDENTIFIED BY <password>
  2. GRANT CONNECT, RESOURCE, and a tablespace quota to that user
  3. GRANT SELECT on ADMIN.recommendations and ADMIN.stock_cap_classification
     to that user (read-only access to shared reference data)
  4. Run the DDL below, schema-qualified to the new tenant user
  5. Insert the tenant's encrypted credentials into ADMIN.tenant_config

The tables/views here are IDENTICAL in shape to v4's schema — no tenant_id
column anywhere, because the schema boundary itself IS the tenant boundary.
This means budget_manager.py's queries barely change: they just run against
a different connection per tenant instead of a single shared one.
"""

def tenant_ddl_statements(schema):
    """Return the list of DDL statements to create one tenant's full schema,
    with all table/view names qualified to `schema` (their DB username)."""
    return [
        f"DROP TABLE {schema}.trades PURGE",
        f"DROP TABLE {schema}.category_allocation PURGE",
        f"DROP TABLE {schema}.portfolio_budget PURGE",

        # ── Portfolio budget — unchanged from v4, just lives in tenant's own schema
        f"""
        CREATE TABLE {schema}.portfolio_budget (
            budget_id       NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            total_budget    NUMBER(14,2) NOT NULL,
            effective_from  DATE DEFAULT SYSDATE,
            is_active       CHAR(1) DEFAULT 'Y' CHECK (is_active IN ('Y','N')),
            created_at      TIMESTAMP DEFAULT SYSTIMESTAMP
        )
        """,

        # ── Category allocation — unchanged from v4
        f"""
        CREATE TABLE {schema}.category_allocation (
            category_id       NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            budget_id         NUMBER REFERENCES {schema}.portfolio_budget(budget_id),
            category_name     VARCHAR2(60) NOT NULL,
            allocation_pct    NUMBER(5,2) NOT NULL,
            large_cap_pct     NUMBER(5,2) DEFAULT 0,
            mid_cap_pct       NUMBER(5,2) DEFAULT 0,
            small_cap_pct     NUMBER(5,2) DEFAULT 0,
            micro_cap_pct     NUMBER(5,2) DEFAULT 0,
            is_active         CHAR(1) DEFAULT 'Y' CHECK (is_active IN ('Y','N')),
            created_at        TIMESTAMP DEFAULT SYSTIMESTAMP
        )
        """,

        # ── Trades — same columns as v4, PLUS rec_id linking back to the
        # shared recommendations table (cross-schema reference; Oracle
        # doesn't allow a FK across schemas without extra grants, so this
        # is a plain NUMBER column, validated at the application layer)
        f"""
        CREATE TABLE {schema}.trades (
            trade_id            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            rec_id              NUMBER,   -- points to ADMIN.recommendations.rec_id
            category_id         NUMBER REFERENCES {schema}.category_allocation(category_id),
            category_name       VARCHAR2(60),
            stock_name          VARCHAR2(120),
            symbol              VARCHAR2(40),
            stock_type          VARCHAR2(20),
            buy_date            DATE,
            recommended_price   NUMBER(12,2),
            target_price        NUMBER(12,2),
            timeframe           VARCHAR2(40),
            have_interest       VARCHAR2(20),
            status              VARCHAR2(20) DEFAULT 'Open',
            target_met          VARCHAR2(10),
            target_met_date     DATE,
            gain                NUMBER(14,2),
            my_buy_date         DATE,
            order_type          VARCHAR2(20),
            buy_order_id        VARCHAR2(40),
            market_price_at_buy NUMBER(12,2),
            my_buy_price        NUMBER(12,2),
            my_buy_qty          NUMBER,
            invested_amount     NUMBER(14,2),
            my_sell_date        DATE,
            my_sell_price       NUMBER(12,2),
            my_sell_qty         NUMBER,
            my_gain_loss        NUMBER(14,2),
            gtt_id              VARCHAR2(40),
            gtt_status          VARCHAR2(20),
            notes               VARCHAR2(500),
            created_at          TIMESTAMP DEFAULT SYSTIMESTAMP,
            updated_at          TIMESTAMP DEFAULT SYSTIMESTAMP
        )
        """,

        f"CREATE INDEX idx_{schema}_trades_status ON {schema}.trades(status)",
        f"CREATE INDEX idx_{schema}_trades_category ON {schema}.trades(category_name)",
        f"CREATE INDEX idx_{schema}_trades_buy_date ON {schema}.trades(buy_date)",
        f"CREATE INDEX idx_{schema}_trades_symbol ON {schema}.trades(symbol)",
        f"CREATE INDEX idx_{schema}_trades_rec_id ON {schema}.trades(rec_id)",
    ]


def tenant_view_statements(schema):
    """Budget views — IDENTICAL logic to v4, no tenant_id filter needed
    since the whole schema already belongs to one tenant."""
    return [
        f"""
        CREATE OR REPLACE VIEW {schema}.category_budget_status AS
        SELECT
            ca.category_name,
            ca.allocation_pct,
            ROUND(pb.total_budget * ca.allocation_pct / 100, 2) AS category_budget,
            NVL(SUM(CASE WHEN t.status = 'Open' THEN t.invested_amount END), 0) AS invested,
            ROUND(pb.total_budget * ca.allocation_pct / 100, 2)
                - NVL(SUM(CASE WHEN t.status = 'Open' THEN t.invested_amount END), 0) AS available
        FROM {schema}.category_allocation ca
        JOIN {schema}.portfolio_budget pb ON pb.budget_id = ca.budget_id AND pb.is_active = 'Y'
        LEFT JOIN {schema}.trades t ON t.category_id = ca.category_id
        WHERE ca.is_active = 'Y'
        GROUP BY ca.category_name, ca.allocation_pct, pb.total_budget
        """,

        f"""
        CREATE OR REPLACE VIEW {schema}.stock_type_budget_status AS
        SELECT
            ca.category_name,
            t.stock_type,
            CASE t.stock_type
                WHEN 'Large Cap' THEN ROUND(pb.total_budget * ca.large_cap_pct / 100, 2)
                WHEN 'Mid Cap'   THEN ROUND(pb.total_budget * ca.mid_cap_pct / 100, 2)
                WHEN 'Small Cap' THEN ROUND(pb.total_budget * ca.small_cap_pct / 100, 2)
                WHEN 'Micro Cap' THEN ROUND(pb.total_budget * ca.micro_cap_pct / 100, 2)
            END AS stock_type_budget,
            NVL(SUM(CASE WHEN t.status = 'Open' THEN t.invested_amount END), 0) AS invested
        FROM {schema}.category_allocation ca
        JOIN {schema}.portfolio_budget pb ON pb.budget_id = ca.budget_id AND pb.is_active = 'Y'
        LEFT JOIN {schema}.trades t ON t.category_id = ca.category_id
        WHERE ca.is_active = 'Y' AND t.stock_type IS NOT NULL
        GROUP BY ca.category_name, t.stock_type, ca.large_cap_pct, ca.mid_cap_pct,
                 ca.small_cap_pct, ca.micro_cap_pct, pb.total_budget
        """,
    ]


def grant_statements(schema, admin_schema):
    """Grants + synonyms so the new tenant can query shared reference tables
    by their bare name (e.g. 'recommendations') instead of needing to fully
    qualify every reference as 'ADMIN.recommendations' in application code.

    GRANT SELECT alone is not enough — Oracle only resolves unqualified table
    names against the CURRENT schema unless a synonym exists pointing
    elsewhere. Without these synonyms, `SELECT * FROM recommendations` run as
    the tenant fails with ORA-00942 even though the GRANT succeeded.
    """
    return [
        f"GRANT SELECT ON {admin_schema}.recommendations TO {schema}",
        f"GRANT SELECT ON {admin_schema}.stock_cap_classification TO {schema}",
        f"CREATE OR REPLACE SYNONYM {schema}.recommendations FOR {admin_schema}.recommendations",
        f"CREATE OR REPLACE SYNONYM {schema}.stock_cap_classification FOR {admin_schema}.stock_cap_classification",
    ]
