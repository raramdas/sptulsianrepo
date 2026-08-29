-- 003_trade_resolved_at.sql
--
-- Records WHEN a human resolved a NEEDS_REVIEW trade's symbol.
--
-- Needed because resolving returns the trade to PENDING_BUY, and the buy
-- query is windowed on buy_date (today, plus retry_days). A tip that sat in
-- review for four days would rejoin the queue already outside that window and
-- never be picked up — silently, since it looks queued.
--
-- The obvious alternative, stamping buy_date to today on resolution, was
-- rejected: buy_date is the date the advisory made the call, and the
-- point-in-time backtest slices price history on it. Moving it would
-- falsify the record to work around a query.
--
-- Safe to re-run: ORA-01430 if the column already exists.

ALTER TABLE trades ADD (resolved_at DATE);

CREATE INDEX idx_trades_resolved_at ON trades (resolved_at);

COMMIT;
