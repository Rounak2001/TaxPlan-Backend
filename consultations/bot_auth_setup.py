import os
import sys
import time
from playwright.sync_api import sync_playwright

# This is where your login data (cookies, storage, etc.) will be saved.
# We use this so the bot doesn't have to log in every single time.
USER_DATA_DIR = os.path.join(os.getcwd(), "google_session")

def setup_auth():
    """
    Launches a visible browser window for you to log in to Google.
    Once you log in, the 'google_session' folder will store your state.
    """
    if not os.path.exists(USER_DATA_DIR):
        os.makedirs(USER_DATA_DIR)
        
    print("\n" + "="*50)
    print("GOOGLE MEET BOT - SESSION SETUP")
    print("="*50)
    print(f"Session data will be stored in: {USER_DATA_DIR}")
    print("\n1. A browser window will open.")
    print("2. Log in to your Google Workspace account.")
    print("3. COMPLETE any MFA (Mobile prompt, SMS, etc.) if asked.")
    print("4. Verify you can access https://meet.google.com/")
    print("5. Once inside, simply CLOSE the browser window.")
    print("="*50 + "\n")
    
    with sync_playwright() as p:
        # We launch a persistent context. Unlike a normal 'incognito' bot,
        # this one writes all data to the folder we specified.
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,  # Visible so you can Type!
            args=[
                "--start-maximized",
                # This helps prevent Google from blocking the browser as a 'bot'
                "--disable-blink-features=AutomationControlled" 
            ]
        )
        
        page = context.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?service=mail")
        
        print("Waiting for you to log in and close the browser...")
        
        # Keep the script running as long as the browser is open
        while len(context.pages) > 0:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                break
        
        context.close()
        print("\nSuccess! Session saved. You can now use the automated bot.")

if __name__ == "__main__":
    setup_auth()
