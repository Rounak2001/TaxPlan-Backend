import requests

# We need to simulate the exact payload. We don't have a valid cookie for a fresh request, 
# but let's see if we get a 403 (unauth) or 400 (bad request parsed before auth)
response = requests.post(
    "http://127.0.0.1:8000/api/auth/identity/upload-doc/",
    json={"s3_path": "identity_documents/1/identity_test.jpg"}
)
print(response.status_code)
print(response.text)
