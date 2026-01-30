import os
import sys
import time
import re
import argparse
from datetime import datetime
from playwright.sync_api import sync_playwright

# Path to the saved session
USER_DATA_DIR = os.path.join(os.getcwd(), "google_session")

# Setup Django for database updates
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django
try:
    django.setup()
    from consultations.models import ConsultationBooking
except Exception as e:
    print(f"Django setup failed: {e}")

def handle_popups(page):
    """
    Looks for and closes common blocking popups in Google Meet.
    """
    popups = [
        {"text": "Got it", "type": "button"},
        {"text": "Dismiss", "type": "button"},
        {"text": "Continue without microphone", "type": "button"},
        {"text": "Continue without camera", "type": "button"},
        {"text": "Continue without audio", "type": "button"},
        {"text": "Allow", "type": "button"},
    ]
    
    found_any = False
    for popup in popups:
        try:
            # Check for button with specific text
            btn = page.get_by_role("button", name=popup["text"], exact=False)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"POPUP DETECTED: {popup['text']}. Clicking to dismiss...")
                page.evaluate("btn => btn.click()", btn.first.element_handle())
                found_any = True
                time.sleep(1)
        except:
            pass
    return found_any

def trigger_recording(meeting_url, headless=True, booking_id=None):
    """
    Automates joining a Google Meet call and starting the native recording.
    """
    if not os.path.exists(USER_DATA_DIR):
        print(f"Error: Session data not found at {USER_DATA_DIR}. Please run bot_auth_setup.py first.")
        return False

    print(f"--- BOT STARTED ---")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {meeting_url}")
    print(f"Mode: {'Headless' if headless else 'Visible'}")
    
    with sync_playwright() as p:
        try:
            # Launch browser with the saved session
            context = p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=headless,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            
            page = context.new_page()
            # Maximize for better visibility
            page.set_viewport_size({"width": 1920, "height": 1080})
            
            # 1. Join the meeting
            print("Navigating to meeting...")
            page.goto(meeting_url)
            time.sleep(5)
            
            # Disable camera and mic
            print("Muting mic and camera...")
            page.keyboard.press("Control+d")
            page.keyboard.press("Control+e")
            time.sleep(2)
            
            # Wake up UI
            page.mouse.move(100, 100)
            page.mouse.move(500, 500)
            
            # Wait for Join Button or Lobby (Host might be late)
            print("Waiting for Join button or Lobby to appear...")
            join_labels = ["Join now", "Ask to join", "Join"]
            joined = False
            
            # We wait up to 10 minutes for the host to start the meeting or let us in
            for attempt in range(60): # 60 * 10s = 10 minutes
                handle_popups(page)
                
                # Check for lobby status
                if page.get_by_text("Someone in the meeting will let you in soon", exact=False).count() > 0:
                    print(f"LOBBY: Waiting to be admitted (Attempt {attempt+1})...")
                    time.sleep(10)
                    continue
                
                # Check for Join buttons
                for label in join_labels:
                    btn = page.get_by_text(label, exact=False)
                    if btn.count() > 0:
                        # Ensure it's not a participant dot or something else
                        if btn.first.is_visible():
                            print(f"Found join button with label: {label}")
                            page.evaluate("btn => btn.click()", btn.first.element_handle())
                            joined = True
                            break
                
                if joined:
                    break
                
                # If Activities or Toolbar is already visible, we are already in
                if page.get_by_label(re.compile("Activities", re.I)).count() > 0:
                    print("Already in the meeting.")
                    joined = True
                    break

                if attempt % 3 == 0: # Every 30 seconds
                    print("Still waiting for host to start meeting or admit bot...")
                
                time.sleep(10)
                # Only reload if we are still on the "pre-join" screen
                if page.get_by_text("Ready to join?", exact=False).count() > 0:
                    page.reload()
                    time.sleep(5)
            
            if not joined:
                print("FAILED: Could not join meeting after 10 minutes.")
                capture_failure(page, "join_timeout")
                return False
                
            print("SUCCESS: Joined meeting. Waiting for UI stabilization...")
            time.sleep(10)
            
            # 2. Continuous Recording Attempt Loop
            print("Entering persistent recording trigger loop (10 minute limit)...")
            recording_started = False
            for r_attempt in range(40): # 40 * 15s = 10 minutes
                handle_popups(page)
                
                # Wake up the toolbar
                page.mouse.move(page.viewport_size["width"] / 2, page.viewport_size["height"] / 2)
                page.mouse.move(page.viewport_size["width"] / 2, page.viewport_size["height"] - 10)
                
                # Check if recording is ALREADY active
                if page.get_by_text("Stop recording", exact=False).count() > 0 or \
                   page.locator("span").get_by_text("REC", exact=False).count() > 0:
                    print("Recording is already active. Success!")
                    recording_started = True
                    break

                # Try to find Recording trigger
                found_trigger = False
                
                # Method A: Activities Menu
                activities_btn = page.get_by_label(re.compile("Activities", re.I))
                if activities_btn.count() > 0 and activities_btn.first.is_visible():
                    try:
                        activities_btn.first.click(timeout=5000)
                        time.sleep(3)
                        recording_option = page.get_by_text("Recording", exact=False)
                        if recording_option.count() > 0:
                            recording_option.first.click(force=True)
                            found_trigger = True
                    except:
                        pass
                
                # Method B: More Options (Three Dots)
                if not found_trigger:
                    toolbar_more_btn = page.locator("div[role='toolbar']").get_by_label(re.compile("More options", re.I))
                    if toolbar_more_btn.count() == 0:
                        toolbar_more_btn = page.get_by_label(re.compile("More options", re.I)).last
                    else:
                        toolbar_more_btn = toolbar_more_btn.first
                    
                    if toolbar_more_btn.count() > 0:
                        page.evaluate("btn => btn.click()", toolbar_more_btn.element_handle())
                        time.sleep(3)
                        
                        # Look for "Record meeting" or "Record"
                        record_menu_item = page.get_by_text("Record meeting", exact=False)
                        if record_menu_item.count() == 0:
                            record_menu_item = page.get_by_text("Recording", exact=False)
                        
                        if record_menu_item.count() > 0:
                            page.evaluate("btn => btn.click()", record_menu_item.first.element_handle())
                            found_trigger = True

                if found_trigger:
                    time.sleep(3)
                    print("Clicking Start Recording button...")
                    start_btns = ["Start recording", "Start", "Start Recording"]
                    start_clicked = False
                    
                    for label in start_btns:
                        btn = page.locator("button").get_by_text(label, exact=False)
                        if btn.count() > 0:
                            for i in range(btn.count()):
                                candidate = btn.nth(i)
                                if candidate.is_visible():
                                    page.evaluate("btn => btn.click()", candidate.element_handle())
                                    start_clicked = True
                                    break
                        if start_clicked: break
                    
                    if start_clicked:
                        time.sleep(3)
                        # Check for confirmation modal
                        confirm_btn = page.get_by_role("button", name="Start", exact=True)
                        if confirm_btn.count() == 0:
                            confirm_btn = page.locator("div[role='dialog'] button").get_by_text("Start", exact=True)
                        
                        if confirm_btn.count() > 0:
                            page.evaluate("btn => btn.click()", confirm_btn.first.element_handle())
                            time.sleep(5)
                        
                        # Verify
                        if page.get_by_text("Stop recording", exact=False).count() > 0 or \
                           page.locator("span").get_by_text("REC", exact=False).count() > 0:
                            recording_started = True
                            print(f"VERIFIED: Recording is now ACTIVE.")
                            break
                        else:
                            print("Trigger sent but recording status not verified yet. Retrying loop...")
                
                if r_attempt % 4 == 0: # Every 1 minute
                    print(f"Still trying to trigger recording (Attempt {r_attempt+1})...")
                
                time.sleep(15)

            if recording_started:
                if booking_id:
                    try:
                        booking = ConsultationBooking.objects.get(id=booking_id)
                        booking.bot_recorded = True
                        booking.save(update_fields=['bot_recorded'])
                        print(f"DATABASE UPDATED: Booking {booking_id} status set to Recorded.")
                    except Exception as db_err:
                        print(f"Database update failed: {db_err}")
                return True
            else:
                print("FAILED: Could not start recording after multiple attempts.")
                capture_failure(page, "recording_trigger_timeout")
                return False

        except Exception as e:
            print(f"CRITICAL ERROR: {str(e)}")
            if 'page' in locals():
                capture_failure(page, "critical_error")
            return False
        finally:
            print("Bot session closing.")
            if 'context' in locals():
                context.close()

def capture_failure(page, name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    path = os.path.join(log_dir, f"failure_{name}_{int(time.time())}.png")
    page.screenshot(path=path)
    print(f"FAILURE SCREENSHOT SAVED: {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Meet Recording Trigger Bot")
    parser.add_argument("url", help="Google Meet URL to join")
    parser.add_argument("--visible", action="store_true", help="Run browser in visible mode")
    parser.add_argument("--booking-id", type=int, help="Optional database ID of the booking")
    args = parser.parse_args()
    
    trigger_recording(args.url, headless=not args.visible, booking_id=args.booking_id)
