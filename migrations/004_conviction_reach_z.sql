-- 004_conviction_reach_z.sql
--
-- Stores the first-passage z-score a conviction run computed:
--     z = gap_to_target / (daily_vol * sqrt(63))
--
-- Kept as its own column rather than left inside layers_json because it is
-- the quantity we intend to validate against realised time-to-target, and
-- that means querying and correlating it directly. Folding it into a JSON
-- blob would make the one measurement we actually care about the hardest
-- thing in the table to get at.
--
-- Null for every row scored before 2026-08-29 and for the full engine, which
-- does not compute it. Old records are deliberately left alone.
--
-- Safe to re-run: ORA-01430 if the column exists.

ALTER TABLE conviction_scores ADD (reach_z NUMBER);

COMMIT;
