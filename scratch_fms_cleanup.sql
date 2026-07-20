-- ============================================================
-- FMS ticket cleanup: deletes all FMS tickets (and dependents)
-- created on or before 2026-07-17, and everything they own.
-- Run in pgAdmin's Query Tool against the OmniFlow database.
-- ============================================================

BEGIN;

-- 1) Preview how many tickets will be deleted (sanity check before committing)
SELECT count(*) AS tickets_to_delete
FROM fms_tickets
WHERE created_at <= '2026-07-17 23:59:59';

-- 2) Delete children first (FK order), all scoped to the same ticket set
DELETE FROM custom_submodule_responses
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM fms_ticket_knowledge_links
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM invoice_records
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM dispatch_records
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM pms_daily_logs
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM fms_field_edit_log
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM fms_split_evidence
WHERE split_id IN (
  SELECT id FROM fms_ticket_splits
  WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
     OR root_ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
);

DELETE FROM fms_ticket_helpers
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM fms_events
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

DELETE FROM fms_stage_history
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
   OR split_id IN (
     SELECT id FROM fms_ticket_splits
     WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
        OR root_ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
   );

-- fms_ticket_splits is self-referencing (parent_split_id) -- delete child splits before parent splits
DELETE FROM fms_ticket_splits
WHERE (ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
    OR root_ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59'))
  AND parent_split_id IS NOT NULL;

DELETE FROM fms_ticket_splits
WHERE ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59')
   OR root_ticket_id IN (SELECT id FROM fms_tickets WHERE created_at <= '2026-07-17 23:59:59');

-- 3) Finally, delete the tickets themselves
DELETE FROM fms_tickets
WHERE created_at <= '2026-07-17 23:59:59';

-- 4) Review the result, then either COMMIT or ROLLBACK
-- COMMIT;
-- ROLLBACK;
