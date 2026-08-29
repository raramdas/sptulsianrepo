-- 005_trade_spt_context.sql
--
-- Captures what SPTulsian told us at the moment of the call. These fields
-- arrive on every scrape and were being discarded.
--
--   spt_market_price_at_call  what the stock traded at when the call was
--                             made, as distinct from recommended_price,
--                             which is what they told us to pay. The gap
--                             between them varies with direction and size
--                             (BHEL called at 434 into a 430.5 market, TD
--                             Power at 741 into 752.7) and is the advisory's
--                             own margin of safety, expressed in a number.
--   spt_below_reco            a flag SPTulsian computes themselves.
--   spt_direction             'Buy' on everything seen so far. A 'Sell' would
--                             currently be parsed and acted on as a buy, so
--                             recording it is a safety measure as much as a
--                             research one.
--   spt_rationale             their written reasoning. Plain text for the
--                             Medium Term section only — Little Gems and Big
--                             Gems ship it as a base64 PNG, so it stays null
--                             for the sections covering nearly every trade.
--
-- Recorded now because scraped history cannot be backfilled: the portal shows
-- only what is live today. Nothing scores on these yet.
--
-- Safe to re-run: ORA-01430 if the columns exist.

ALTER TABLE trades ADD (
    spt_market_price_at_call  NUMBER,
    spt_below_reco            NUMBER,
    spt_direction             VARCHAR2(16),
    spt_rationale             VARCHAR2(2000)
);

COMMIT;
