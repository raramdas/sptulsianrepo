-- 002_conviction_model.sql
--
-- Adds `model` to conviction_scores so lite and full scores can be told
-- apart. They are computed from different inputs on different scales and
-- pooling them in a backtest would silently mix two populations.
--
-- Existing rows predate the lite engine, so they are all 'full'.
--
-- Safe to re-run: the ALTER errors with ORA-01430 if the column exists.

ALTER TABLE conviction_scores ADD (model VARCHAR2(16) DEFAULT 'full');

UPDATE conviction_scores SET model = 'full' WHERE model IS NULL;

CREATE INDEX idx_conv_model ON conviction_scores (model, symbol);

COMMIT;
