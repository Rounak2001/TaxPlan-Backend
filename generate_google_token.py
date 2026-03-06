import os
from google_auth_oauthlib.flow import InstalledAppFlow

# The scope for Google Calendar Events
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

# FIXED PORT — must be added to your OAuth client's authorized redirect URIs:
# http://localhost:8765/
PORT = 8765

def main():
    client_secret_path = os.path.join(os.path.dirname(__file__), 'client_secret.json')
    
    if not os.path.exists(client_secret_path):
        print(f"Error: {client_secret_path} not found.")
        return

    print("\n" + "="*60)
    print("IMPORTANT: Before authorizing, make sure you have added")
    print(f"  http://localhost:{PORT}/")
    print("to your OAuth 2.0 client's authorized redirect URIs in")
    print("Google Cloud Console > APIs & Services > Credentials")
    print("="*60 + "\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_path, SCOPES)
    
    # Use a FIXED port so the redirect URI is predictable and can be whitelisted.
    # access_type='offline' ensures we get a refresh token.
    # prompt='consent' ensures a refresh token is ALWAYS returned.
    creds = flow.run_local_server(port=PORT, access_type='offline', prompt='consent')
    
    print("\n" + "="*60)
    print("SUCCESS! NEW REFRESH TOKEN GENERATED:")
    print("="*60)
    print(creds.refresh_token)
    print("="*60)
    print("\nAction Required:")
    print("Copy the token above and update your .env file:")
    print("  GOOGLE_OAUTH_REFRESH_TOKEN=<paste_token_here>")
    print("\nThen restart the Django server for changes to take effect.")
    print("="*60)

if __name__ == '__main__':
    main()
