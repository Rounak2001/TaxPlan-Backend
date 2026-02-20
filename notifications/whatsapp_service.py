import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

def send_whatsapp_template(phone_number, template_name, variables=None):
    """
    Send a WhatsApp template message via Meta Cloud API.
    
    Args:
        phone_number (str): The recipient's phone number (with country code, e.g. "919876543210")
        template_name (str): The name of the approved template in Meta Business Manager
        variables (list): List of string variables to replace {{1}}, {{2}}, etc. in the template
    """
    if not phone_number:
        logger.warning(f"Cannot send WhatsApp template '{template_name}': No phone number provided.")
        return False, "No phone number"

    phone_number_id = settings.META_PHONE_NUMBER_ID
    access_token = settings.META_ACCESS_TOKEN
    api_version = settings.META_API_VERSION

    if not phone_number_id or not access_token:
        logger.error(f"Cannot send WhatsApp template '{template_name}': Meta credentials not configured.")
        return False, "Meta credentials missing"

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    
    # Format phone: ensure no '+' prefix for the API
    formatted_phone = str(phone_number).lstrip('+')
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    # Construct template payload
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
        }
    }
    
    # Add variables to body components if provided
    if variables:
        body_parameters = [{"type": "text", "text": str(var)[:100]} for var in variables] # Meta limits string vars
        payload["template"]["components"] = [
            {
                "type": "body",
                "parameters": body_parameters
            }
        ]
        
    try:
        print(f"\n[WHATSAPP DEBUG] Sending template '{template_name}' to {formatted_phone}")
        print(f"[WHATSAPP DEBUG] Payload: {payload}")
        logger.debug(f"WhatsApp Payload: {payload}")

        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response_data = response.json()
        
        print(f"[WHATSAPP DEBUG] Response Status: {response.status_code}")
        print(f"[WHATSAPP DEBUG] Response Data: {response_data}\n")

        if response.status_code == 200:
            message_id = response_data.get('messages', [{}])[0].get('id', 'unknown')
            logger.info(f"WhatsApp Template '{template_name}' sent securely to {formatted_phone[-4:]}, id: {message_id}")
            return True, "Template sent successfully"
        else:
            error = response_data.get('error', {})
            logger.error(f"WhatsApp API Error sending '{template_name}': {error.get('message')} (Code: {error.get('code')})")
            return False, error.get('message', 'Unknown error')

    except Exception as e:
        print(f"\n[WHATSAPP DEBUG] EXCEPTION: {str(e)}\n")
        logger.exception(f"Exception sending WhatsApp Template '{template_name}': {str(e)}")
        return False, str(e)

