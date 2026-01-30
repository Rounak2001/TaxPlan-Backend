import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Scopes required for creating calendar events and meet links
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_refresh_token():
    # Load client secrets from environment or hardcode for this script
    # These can stay the same as your existing Google Project
    client_id = input("Enter your GOOGLE_OAUTH_CLIENT_ID: ").strip()
    client_secret = input("Enter your GOOGLE_OAUTH_CLIENT_SECRET: ").strip()

    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    
    # This will open a browser window
    print("\nIMPORTANT: Log in with your BUSINESS/WORKSPACE account in the browser.")
    creds = flow.run_local_server(port=0)

    print("\n--- NEW REFRESH TOKEN ---")
    print(creds.refresh_token)
    print("-------------------------\n")
    print("Action Required: Copy the token above and update GOOGLE_OAUTH_REFRESH_TOKEN in your .env file.")

if __name__ == "__main__":
    get_refresh_token()
