-- ============================================
-- Add Consultant
-- ============================================
INSERT INTO consultants_consultantserviceprofile (
    user_id, full_name, email, phone, qualification, 
    experience_years, is_active, max_concurrent_clients, 
    current_client_count, created_at, updated_at
) VALUES (
    1, 'John Doe CA', 'john@example.com', '9876543210',
    'Chartered Accountant', 5, 1, 10, 0,
    datetime('now'), datetime('now')
);

-- ============================================
-- Assign Services to Consultant
-- ============================================
-- Single service
INSERT INTO consultants_consultantserviceexpertise (consultant_id, service_id, added_at)
VALUES (1, 2, datetime('now'));

-- Multiple services at once
INSERT INTO consultants_consultantserviceexpertise (consultant_id, service_id, added_at)
VALUES 
    (1, 2, datetime('now')),
    (1, 3, datetime('now')),
    (1, 11, datetime('now'));

-- All services from a category
INSERT INTO consultants_consultantserviceexpertise (consultant_id, service_id, added_at)
SELECT 1, id, datetime('now')
FROM consultants_service
WHERE category_id = (SELECT id FROM consultants_servicecategory WHERE name = 'Income Tax');

-- ============================================
-- Client Requests Multiple Services
-- ============================================
INSERT INTO consultants_clientservicerequest (
    client_id, service_id, status, notes, priority, created_at, updated_at
) VALUES 
    (3, 2, 'pending', 'Income Tax E-Filing needed', 5, datetime('now'), datetime('now')),
    (3, 11, 'pending', 'GST Registration needed', 3, datetime('now'), datetime('now')),
    (3, 20, 'pending', 'PAN Registration needed', 1, datetime('now'), datetime('now'));

-- ============================================
-- Assign Consultant to Request
-- ============================================
-- Find available consultant
SELECT cp.id, cp.full_name, cp.current_client_count
FROM consultants_consultantserviceprofile cp
JOIN consultants_consultantserviceexpertise cse ON cp.id = cse.consultant_id
WHERE cse.service_id = 2
  AND cp.is_active = 1
  AND cp.current_client_count < cp.max_concurrent_clients
ORDER BY cp.current_client_count ASC
LIMIT 1;

-- Assign consultant
UPDATE consultants_clientservicerequest
SET assigned_consultant_id = 1, status = 'assigned', assigned_at = datetime('now')
WHERE id = 1;

-- Increment workload
UPDATE consultants_consultantserviceprofile
SET current_client_count = current_client_count + 1
WHERE id = 1;

-- ============================================
-- View Requests
-- ============================================
SELECT 
    csr.id,
    s.title as service,
    csr.status,
    cp.full_name as consultant,
    csr.created_at
FROM consultants_clientservicerequest csr
JOIN consultants_service s ON csr.service_id = s.id
LEFT JOIN consultants_consultantserviceprofile cp ON csr.assigned_consultant_id = cp.id
WHERE csr.client_id = 3;
