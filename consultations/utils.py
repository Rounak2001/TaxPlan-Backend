import subprocess
import os
import sys
import logging
import time

logger = logging.getLogger(__name__)

def trigger_recording_bot(meeting_url, booking_id=None):
    """
    Launches the meet_trigger.py script in a background process.
    """
    if not meeting_url:
        logger.warning("No meeting URL provided to trigger_recording_bot")
        return False

    # Path to the trigger script
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, "meet_trigger.py")
    
    python_executable = sys.executable
    
    try:
        # Launching as a background process using Popen
        # We redirect stdout/stderr to a log file to avoid hanging
        log_dir = os.path.join(base_dir, "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        log_file = os.path.join(log_dir, f"bot_{int(time.time())}.log")
        
        # Prepare command
        cmd = [python_executable, script_path, meeting_url]
        if booking_id:
            cmd.extend(["--booking-id", str(booking_id)])

        with open(log_file, "w") as f:
            process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                start_new_session=True # Detach from parent
            )
        
        logger.info(f"Triggered recording bot for {meeting_url} (PID: {process.pid}, Log: {log_file})")
        return True
    except Exception as e:
        logger.error(f"Failed to trigger recording bot: {str(e)}")
        return False
