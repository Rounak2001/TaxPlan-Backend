import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# If modifying these SCOPES, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def main():
    """
    To use this script:
    1. Go to Google Cloud Console -> APIs & Services -> Credentials.
    2. Create OAuth 2.0 Client ID (Desktop App).
    3. Download the JSON and save it as 'client_secret.json' in this folder.
    4. Run this script.
    """
    client_secret_file = 'client_secret.json'
    
    if not os.path.exists(client_secret_file):
        print(f"Error: {client_secret_file} not found.")
        print("Please download your OAuth client secret JSON from Google Cloud Console and rename it to 'client_secret.json'.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n--- GOOGLE OAUTH CREDENTIALS ---")
    print(f"GOOGLE_OAUTH_CLIENT_ID='{creds.client_id}'")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET='{creds.client_secret}'")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN='{creds.refresh_token}'")
    print("--------------------------------\n")
    
    print("Add these to your .env file in the Backend folder.")

if __name__ == '__main__':
    main()
