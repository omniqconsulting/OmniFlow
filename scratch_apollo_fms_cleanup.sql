-- ============================================================
-- Apollo Industries -- delete ALL fms_tickets data (every status,
-- every date) and every dependent child row, in one script.
-- Run in pgAdmin's Query Tool.
-- ============================================================

BEGIN;

WITH tenant AS (
  SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%'
),
tix AS (
  SELECT id FROM fms_tickets WHERE tenant_id IN (SELECT id FROM tenant)
),
splits AS (
  SELECT id FROM fms_ticket_splits
  WHERE ticket_id IN (SELECT id FROM tix) OR root_ticket_id IN (SELECT id FROM tix)
),
del_1 AS (DELETE FROM custom_submodule_responses   WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_2 AS (DELETE FROM fms_ticket_knowledge_links    WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_3 AS (DELETE FROM invoice_records               WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_4 AS (DELETE FROM dispatch_records              WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_5 AS (DELETE FROM pms_daily_logs                WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_6 AS (DELETE FROM fms_field_edit_log            WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_7 AS (DELETE FROM fms_split_evidence            WHERE split_id  IN (SELECT id FROM splits) RETURNING 1),
del_8 AS (DELETE FROM fms_ticket_helpers            WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_9 AS (DELETE FROM fms_events                    WHERE ticket_id IN (SELECT id FROM tix) RETURNING 1),
del_10 AS (DELETE FROM fms_stage_history            WHERE ticket_id IN (SELECT id FROM tix) OR split_id IN (SELECT id FROM splits) RETURNING 1),
del_11 AS (DELETE FROM fms_ticket_splits            WHERE id IN (SELECT id FROM splits) AND parent_split_id IS NOT NULL RETURNING 1),
del_12 AS (DELETE FROM fms_ticket_splits            WHERE id IN (SELECT id FROM splits) RETURNING 1),
del_13 AS (DELETE FROM fms_tickets                  WHERE id IN (SELECT id FROM tix) RETURNING 1)
SELECT
  (SELECT count(*) FROM del_1)  AS custom_submodule_responses_deleted,
  (SELECT count(*) FROM del_2)  AS fms_ticket_knowledge_links_deleted,
  (SELECT count(*) FROM del_3)  AS invoice_records_deleted,
  (SELECT count(*) FROM del_4)  AS dispatch_records_deleted,
  (SELECT count(*) FROM del_5)  AS pms_daily_logs_deleted,
  (SELECT count(*) FROM del_6)  AS fms_field_edit_log_deleted,
  (SELECT count(*) FROM del_7)  AS fms_split_evidence_deleted,
  (SELECT count(*) FROM del_8)  AS fms_ticket_helpers_deleted,
  (SELECT count(*) FROM del_9)  AS fms_events_deleted,
  (SELECT count(*) FROM del_10) AS fms_stage_history_deleted,
  (SELECT count(*) FROM del_11) AS fms_ticket_child_splits_deleted,
  (SELECT count(*) FROM del_12) AS fms_ticket_splits_deleted,
  (SELECT count(*) FROM del_13) AS fms_tickets_deleted;

-- Review the returned counts, then:
-- COMMIT;
-- ROLLBACK;
