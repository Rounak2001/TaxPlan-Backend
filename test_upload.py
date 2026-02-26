import requests

response = requests.post(
    "http://127.0.0.1:8000/api/auth/identity/upload-doc/",
    json={"s3_path": "identity_documents/1/identity_test.jpg"},
    cookies={"applicant_token": "some_cookie"} # We need a valid applicant token...
)
print(response.status_code)
print(response.text)
