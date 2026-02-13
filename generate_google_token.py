import os
from google_auth_oauthlib.flow import InstalledAppFlow

# The scope for Google Calendar Events
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def main():
    client_secret_path = os.path.join(os.path.dirname(__file__), 'client_secret.json')
    
    if not os.path.exists(client_secret_path):
        print(f"Error: {client_secret_path} not found.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_path, SCOPES)
    
    # This will open a browser window for you to log in
    # Use port 0 to find any available port
    creds = flow.run_local_server(port=0)
    
    print("\n" + "="*50)
    print("NEW REFRESH TOKEN GENERATED:")
    print("="*50)
    print(creds.refresh_token)
    print("="*50)
    print("\nAction Required:")
    print("Copy the token above and paste it into your .env file as:")
    print("GOOGLE_OAUTH_REFRESH_TOKEN=[the_token_above]")
    print("="*50)

if __name__ == '__main__':
    main()
