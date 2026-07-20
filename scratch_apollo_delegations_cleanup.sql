-- ============================================================
-- Apollo Industries "Flow Delegations" cleanup (legacy `tickets` table,
-- NOT fms_tickets -- the dashboard card reads Ticket model rows).
-- Run in pgAdmin's Query Tool.
-- ============================================================

BEGIN;

-- 0) Find the tenant id
SELECT id AS tenant_id, name FROM tenants WHERE name ILIKE '%apolo%industries%';

-- 1) Preview: should sum to ~50 (matches the dashboard card)
SELECT
  count(*) FILTER (WHERE status NOT IN ('CLOSED','DONE')) AS open_active,
  count(*) FILTER (WHERE status IN ('DONE','CLOSED'))     AS done_closed,
  count(*) AS total
FROM tickets
WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%')
  AND is_deleted = false;

-- 2) Delete children first (FK order)
DELETE FROM ticket_knowledge_links
WHERE ticket_id IN (
  SELECT id FROM tickets WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%')
);

DELETE FROM ticket_comments
WHERE ticket_id IN (
  SELECT id FROM tickets WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%')
);

DELETE FROM ticket_events
WHERE ticket_id IN (
  SELECT id FROM tickets WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%')
);

DELETE FROM ticket_assignees
WHERE ticket_id IN (
  SELECT id FROM tickets WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%')
);

-- 3) Delete the tickets themselves (covers ALL delegations for this tenant,
--    both open and closed, since no date filter is applied)
DELETE FROM tickets
WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%');

-- 4) Verify count is now 0
SELECT count(*) FROM tickets
WHERE tenant_id IN (SELECT id FROM tenants WHERE name ILIKE '%apolo%industries%');

-- COMMIT;
-- ROLLBACK;
