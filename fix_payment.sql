-- Manual fix: Create service request for completed payment (Order ID: 4)
-- This is needed because the payment was completed before the integration was added

-- First, find the service ID for "PF Registration"
SELECT id, title FROM consultants_service WHERE title LIKE '%PF Registration%';

-- Assuming service_id = 38 (PF Registration from Compliance category)
-- Create service request for user_id = 5
INSERT INTO consultants_clientservicerequest (
    client_id,
    service_id,
    status,
    notes,
    priority,
    created_at,
    updated_at
) VALUES (
    5,  -- user_id from order
    38,  -- Replace with actual service_id from above query
    'pending',
    'Payment completed for order #4 - Manual entry',
    5,
    datetime('now'),
    datetime('now')
);

-- Get the request_id that was just created
SELECT id FROM consultants_clientservicerequest ORDER BY id DESC LIMIT 1;

-- Find available consultant for PF Registration
SELECT 
    cp.id,
    cp.full_name,
    cp.current_client_count,
    cp.max_concurrent_clients
FROM consultants_consultantserviceprofile cp
JOIN consultants_consultantserviceexpertise cse ON cp.id = cse.consultant_id
WHERE cse.service_id = 38  -- PF Registration
  AND cp.is_active = 1
  AND cp.current_client_count < cp.max_concurrent_clients
ORDER BY cp.current_client_count ASC
LIMIT 1;

-- If consultant found (e.g., consultant_id = 1), assign manually
UPDATE consultants_clientservicerequest
SET 
    assigned_consultant_id = 1,  -- Replace with consultant_id from above
    status = 'assigned',
    assigned_at = datetime('now'),
    updated_at = datetime('now')
WHERE id = (SELECT id FROM consultants_clientservicerequest ORDER BY id DESC LIMIT 1);

-- Increment consultant workload
UPDATE consultants_consultantserviceprofile
SET current_client_count = current_client_count + 1
WHERE id = 1;  -- Replace with consultant_id

-- Verify the assignment
SELECT 
    csr.id,
    s.title as service,
    csr.status,
    cp.full_name as consultant,
    cp.email,
    cp.phone
FROM consultants_clientservicerequest csr
JOIN consultants_service s ON csr.service_id = s.id
LEFT JOIN consultants_consultantserviceprofile cp ON csr.assigned_consultant_id = cp.id
WHERE csr.client_id = 5
ORDER BY csr.created_at DESC;
