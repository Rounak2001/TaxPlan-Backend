import requests

# We bypass auth locally just to trace down where the 500 happens inside the view if it's a code crash
try:
    resp = requests.post("http://127.0.0.1:8000/api/assessment/sessions/", 
                          json={"selected_tests": ["GST"]},
                          cookies={"applicant_token": "some_token_here"})
    # It might say 401 if it stops early. Let's see. 
    print(resp.status_code, resp.text[:200])
except Exception as e:
    print(e)
