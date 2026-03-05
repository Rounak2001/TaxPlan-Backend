import random
import time
import requests
import logging
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

# ─── Constants ───
OTP_LENGTH = 6
OTP_EXPIRY_SECONDS = 600        # 10 minutes
RESEND_COOLDOWN_SECONDS = 30    # 30 seconds between resends
MAX_VERIFY_ATTEMPTS = 5         # Max wrong OTP attempts
MAX_RESENDS_PER_HOUR = 5        # Max OTP sends per phone per hour

# ─── Cache key helpers ───
def _otp_key(phone):
    return f"otp:{phone}"

def _attempts_key(phone):
    return f"otp_attempts:{phone}"

def _resend_cooldown_key(phone):
    return f"otp_cooldown:{phone}"

def _resend_count_key(phone):
    return f"otp_resend_count:{phone}"


def generate_otp():
    """Generate a random 6-digit OTP."""
    return ''.join([str(random.randint(0, 9)) for _ in range(OTP_LENGTH)])


def store_otp(phone_number, otp):
    """Store OTP in cache with expiry and reset attempt counter."""
    cache.set(_otp_key(phone_number), otp, OTP_EXPIRY_SECONDS)
    cache.set(_attempts_key(phone_number), 0, OTP_EXPIRY_SECONDS)
    # Set resend cooldown (30 seconds)
    cache.set(_resend_cooldown_key(phone_number), time.time(), RESEND_COOLDOWN_SECONDS)
    # Increment hourly resend counter
    resend_key = _resend_count_key(phone_number)
    count = cache.get(resend_key, 0)
    cache.set(resend_key, count + 1, 3600)  # 1 hour TTL


def can_resend_otp(phone_number):
    """
    Check if OTP can be resent.
    Returns (can_resend: bool, reason: str, wait_seconds: int)
    """
    # Check cooldown
    cooldown_time = cache.get(_resend_cooldown_key(phone_number))
    if cooldown_time:
        elapsed = time.time() - cooldown_time
        remaining = int(RESEND_COOLDOWN_SECONDS - elapsed)
        if remaining > 0:
            return False, f"Please wait {remaining} seconds before requesting a new OTP", remaining

    # Check hourly limit
    resend_count = cache.get(_resend_count_key(phone_number), 0)
    if resend_count >= MAX_RESENDS_PER_HOUR:
        return False, "Too many OTP requests. Please try again after some time.", 0

    return True, "", 0


def send_whatsapp_otp(phone_number, otp):
    """
    Send OTP via Meta WhatsApp Cloud API using the 'otp_authentication' authentication template.
    phone_number should be in format '919876543210' (no + prefix for the API).
    """
    phone_number_id = settings.META_PHONE_NUMBER_ID
    access_token = settings.META_ACCESS_TOKEN
    api_version = settings.META_API_VERSION

    if not phone_number_id or not access_token:
        logger.error("Meta WhatsApp API credentials not configured")
        return False, "WhatsApp service not configured. Please contact support."

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"

    # Format phone: ensure no '+' prefix for the API
    formatted_phone = phone_number.lstrip('+')

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # Authentication template with Body + URL button (One-Tap Autofill)
    # DIAGNOSTIC CONFIRMED: This specific template 'otp_authentication' requires both Body and URL params
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "template",
        "template": {
            "name": "otp_authentication",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": otp}
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [
                        {"type": "text", "text": otp}
                    ]
                }
            ]
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response_data = response.json()

        if response.status_code == 200:
            message_id = response_data.get('messages', [{}])[0].get('id', 'unknown')
            logger.info(f"WhatsApp OTP sent successfully to {phone_number[-4:]}, message_id: {message_id}")
            return True, "OTP sent successfully"
        else:
            error = response_data.get('error', {})
            error_msg = error.get('message', 'Unknown error')
            error_code = error.get('code', 'N/A')
            logger.error(f"WhatsApp API error {error_code}: {error_msg}")

            # User-friendly error messages
            if error_code == 131026:
                return False, "This phone number is not registered on WhatsApp. Please use a WhatsApp-enabled number."
            elif error_code == 131047:
                return False, "Too many messages sent. Please try again later."
            elif error_code in (190, 10):
                return False, "WhatsApp service authentication failed. Please contact support."
            else:
                return False, f"Failed to send OTP. Please try again. (Error: {error_code})"

    except requests.Timeout:
        logger.error("WhatsApp API request timed out")
        return False, "WhatsApp service is taking too long. Please try again."
    except requests.RequestException as e:
        logger.error(f"WhatsApp API request failed: {str(e)}")
        return False, "Unable to reach WhatsApp service. Please check your connection and try again."


def verify_otp(phone_number, otp):
    """
    Verify OTP for a phone number.
    Returns (success: bool, message: str, remaining_attempts: int)
    """
    stored_otp = cache.get(_otp_key(phone_number))

    if stored_otp is None:
        return False, "OTP has expired. Please request a new one.", 0

    # Check attempts
    attempts = cache.get(_attempts_key(phone_number), 0)
    if attempts >= MAX_VERIFY_ATTEMPTS:
        # Clear the OTP — force re-request
        cache.delete(_otp_key(phone_number))
        cache.delete(_attempts_key(phone_number))
        return False, "Too many failed attempts. Please request a new OTP.", 0

    if str(otp) != str(stored_otp):
        attempts += 1
        cache.set(_attempts_key(phone_number), attempts, OTP_EXPIRY_SECONDS)
        remaining = MAX_VERIFY_ATTEMPTS - attempts
        if remaining <= 0:
            cache.delete(_otp_key(phone_number))
            cache.delete(_attempts_key(phone_number))
            return False, "Too many failed attempts. Please request a new OTP.", 0
        return False, f"Invalid OTP. {remaining} attempt(s) remaining.", remaining

    # ✅ OTP matches — cleanup cache
    cache.delete(_otp_key(phone_number))
    cache.delete(_attempts_key(phone_number))
    cache.delete(_resend_cooldown_key(phone_number))
    cache.delete(_resend_count_key(phone_number))

    return True, "Phone number verified successfully!", MAX_VERIFY_ATTEMPTS
