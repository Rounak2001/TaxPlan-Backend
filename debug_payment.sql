-- Check if payment was successful
SELECT id, user_id, total_amount, status, razorpay_order_id, razorpay_payment_id, created_at
FROM service_orders_serviceorder
ORDER BY created_at DESC
LIMIT 5;

-- Check order items
SELECT oi.id, oi.order_id, oi.service_id, oi.service_title, oi.price
FROM service_orders_orderitem oi
JOIN service_orders_serviceorder so ON oi.order_id = so.id
WHERE so.status = 'paid'
ORDER BY so.created_at DESC;

-- Check if service requests were created
SELECT id, client_id, service_id, status, assigned_consultant_id, created_at
FROM consultants_clientservicerequest
ORDER BY created_at DESC
LIMIT 10;

-- Check consultant assignments
SELECT 
    csr.id as request_id,
    u.email as client_email,
    s.title as service,
    csr.status,
    cp.full_name as consultant,
    csr.created_at
FROM consultants_clientservicerequest csr
JOIN core_auth_user u ON csr.client_id = u.id
JOIN consultants_service s ON csr.service_id = s.id
LEFT JOIN consultants_consultantserviceprofile cp ON csr.assigned_consultant_id = cp.id
ORDER BY csr.created_at DESC
LIMIT 10;
