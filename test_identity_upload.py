import requests

# 1. First get the S3 path
try:
    resp1 = requests.post("http://127.0.0.1:8000/api/auth/identity/get-upload-url/", 
                          json={"file_ext": "jpg", "content_type": "image/jpeg"},
                          cookies={"applicant_token": "some_token_here"})
    print("GET UPLOAD URL:", resp1.status_code, resp1.text)
    
except Exception as e:
    print("Error:", e)
