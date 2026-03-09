import json
import logging
import requests
import os
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

logger = logging.getLogger(__name__)

class WhatsAppWebhookView(APIView):
    """
    Webhook endpoint for Meta WhatsApp Cloud API to receive inbound messages.
    """
    permission_classes = [AllowAny]  # Meta needs to access this without auth

    def get(self, request):
        """
        Handle Meta's webhook verification challenge.
        """
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        verify_token = os.getenv('META_WEBHOOK_VERIFY_TOKEN')
        
        if mode and token:
            if mode == 'subscribe' and token == verify_token:
                logger.info("WhatsApp WEBHOOK_VERIFIED")
                return HttpResponse(challenge, status=200)
            else:
                logger.warning("WhatsApp WEBHOOK_VERIFICATION_FAILED")
                return HttpResponse('Forbidden', status=403)
        return HttpResponse('Bad Request', status=400)

    def post(self, request):
        """
        Handle incoming WhatsApp messages.
        """
        try:
            body = json.loads(request.body)
            # Check if this is a WhatsApp API event
            if body.get('object') == 'whatsapp_business_account':
                for entry in body.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        
                        
                        # Handle incoming messages
                        if 'messages' in value:
                            for message in value['messages']:
                                self.process_message(message, value.get('contacts', [{}])[0])
                                
                        # Handle message status updates (Read Receipts)
                        if 'statuses' in value:
                            for status in value['statuses']:
                                if status.get('status') == 'read':
                                    self.process_read_receipt(status)
                
                return HttpResponse('EVENT_RECEIVED', status=200)
            else:
                return HttpResponse('NOT_FOUND', status=404)
                
        except json.JSONDecodeError:
            return HttpResponse('BAD_REQUEST', status=400)
        except Exception as e:
            logger.error(f"Error handling WhatsApp webhook: {str(e)}")
            return HttpResponse('INTERNAL_SERVER_ERROR', status=500)

    def process_message(self, message, contact):
        """
        Process an individual incoming message.
        """
        phone_number = contact.get('wa_id')
        name = contact.get('profile', {}).get('name', 'Unknown')
        
        msg_type = message.get('type')
        
        # Extract text content
        content = ""
        if msg_type == 'text':
            content = message.get('text', {}).get('body', '').strip()
        elif msg_type == 'interactive':
            interactive = message.get('interactive', {})
            interactive_type = interactive.get('type')
            if interactive_type == 'list_reply':
                content = interactive.get('list_reply', {}).get('id', '')
            elif interactive_type == 'button_reply':
                content = interactive.get('button_reply', {}).get('id', '')
        
        logger.info(f"Received {msg_type} message from {phone_number} ({name}): {content[:50]}")
        
        # Dispatch to async handler to avoid blocking the webhook response
        from django.core.cache import cache
        from core_auth.models import User
        from chat.models import Conversation, Message
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        
        # Format phone to match DB (assumes DB stores with + or just matches suffix)
        # Search for user by phone (simple right 10 digits match)
        users = User.objects.filter(role=User.CLIENT)
        client = None
        for u in users:
            if u.phone_number and u.phone_number.replace('+', '').replace(' ', '')[-10:] == phone_number[-10:]:
                client = u
                break
                
        if not client:
            logger.warning(f"No client found for phone {phone_number}")
            self.send_whatsapp_text(phone_number, "Sorry, we couldn't find an account associated with this phone number. Please register first.")
            return

        # Handle #switch or #menu keyword
        session_key = f"wa_session_{client.id}"
        if content.lower() in ['#switch', '#menu', 'menu', 'switch']:
            cache.delete(session_key)
            self.send_consultant_menu(client, phone_number)
            return
            
        # Is there a list_reply selection?
        if msg_type == 'interactive' and content.startswith('select_consultant_'):
            consultant_id = content.replace('select_consultant_', '')
            try:
                # Verify conversation exists
                conv = Conversation.objects.get(client=client, consultant_id=consultant_id)
                cache.set(session_key, conv.id, 86400)  # 24 hour session
                self.send_whatsapp_text(phone_number, f"✅ You are now chatting with {conv.consultant.first_name or conv.consultant.username}. Send your message.")
            except Conversation.DoesNotExist:
                self.send_whatsapp_text(phone_number, "Invalid selection. Please try again with #menu.")
            return

        # Check Active Session
        active_conv_id = cache.get(session_key)
        
        if not active_conv_id:
            # Need to pick a consultant
            active_conversations = Conversation.objects.filter(client=client)
            
            if active_conversations.count() == 0:
                # Fallback: Route to an Admin if the client has no consultants assigned
                from core_auth.models import User
                first_admin = User.objects.filter(role=User.ADMIN, is_active=True).first()
                if first_admin:
                    # Create a default conversation with the admin
                    conv, created = Conversation.objects.get_or_create(
                        client=client,
                        consultant=first_admin
                    )
                    cache.set(session_key, conv.id, 86400)
                    active_conv_id = conv.id
                    logger.info(f"Auto-routed unassigned client {phone_number} to Admin {first_admin.username}")
                else:
                    logger.warning(f"No active consultants or admins found for client {phone_number}")
                    self.send_whatsapp_text(phone_number, "We are currently unavailable. Please try again later.")
                    return
            elif active_conversations.count() == 1:
                # Auto-select the only conversation
                conv = active_conversations.first()
                cache.set(session_key, conv.id, 86400)
                active_conv_id = conv.id
                logger.info(f"Auto-routed message from {phone_number} to sole consultant {conv.consultant.username}")
            else:
                # Multiple consultants, require selection
                self.send_consultant_menu(client, phone_number)
                return

        # We have an active conversation, save the message
        try:
            conv = Conversation.objects.get(id=active_conv_id)
            
            if not content and msg_type not in ['text', 'interactive']:
                # Handle images/docs later, just say acknowledged for now
                content = f"[Sent a {msg_type} message outside dashboard]"
                
            # Create message in DB
            msg = Message.objects.create(
                conversation=conv,
                sender=client,
                content=content
            )
            conv.save(update_fields=['updated_at'])
            
            logger.info(f"Saved WhatsApp msg to DB: {msg.id}")
            
            # Broadcast via Channels
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_{conv.id}',
                {
                    'type': 'chat_message',
                    'id': msg.id,
                    'sender_id': client.id,
                    'sender_username': client.username,
                    'content': msg.content,
                    'timestamp': msg.timestamp.isoformat(),
                    'is_read': False,
                }
            )
            
        except Conversation.DoesNotExist:
            cache.delete(session_key)
            self.send_consultant_menu(client, phone_number)

    def send_whatsapp_text(self, phone_number, text):
        """Helper to send a free-form WhatsApp text message"""
        phone_number_id = settings.META_PHONE_NUMBER_ID
        access_token = settings.META_ACCESS_TOKEN
        api_version = settings.META_API_VERSION
        
        if not phone_number_id or not access_token:
            return
            
        url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": text}
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response_data = response.json()
            if response.status_code == 200:
                logger.info(f"WhatsApp text sent securely to {phone_number[-4:]}")
                return True, None
            else:
                error = response_data.get('error', {})
                logger.error(f"WhatsApp API Error sending text: {error.get('message')} (Code: {error.get('code')})")
                return False, error
        except Exception as e:
            logger.error(f"Error sending WA text: {e}")
            return False, {'message': str(e), 'code': -1}

    def send_consultant_menu(self, client, phone_number):
        """Send an Interactive List Message to choose a consultant"""
        from chat.models import Conversation
        
        active_conversations = Conversation.objects.filter(client=client).select_related('consultant')
        
        if active_conversations.count() == 0:
            self.send_whatsapp_text(phone_number, "You have no active consultant chats.")
            return
            
        rows = []
        for conv in active_conversations:
            name = conv.consultant.first_name or conv.consultant.username
            role = "Consultant"
            rows.append({
                "id": f"select_consultant_{conv.consultant.id}",
                "title": name[:24],
                "description": role[:72]
            })
            
        # Limit rows to 10 for interactive messages
        rows = rows[:10]
        
        phone_number_id = settings.META_PHONE_NUMBER_ID
        access_token = settings.META_ACCESS_TOKEN
        api_version = settings.META_API_VERSION
        
        if not phone_number_id or not access_token:
            return
            
        url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Select Consultant"
                },
                "body": {
                    "text": "Who would you like to message?"
                },
                "footer": {
                    "text": "Reply #switch to change anytime"
                },
                "action": {
                    "button": "Choose Consultant",
                    "sections": [
                        {
                            "title": "Your Active Consultants",
                            "rows": rows
                        }
                    ]
                }
            }
        }
        
        try:
            requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.error(f"Error sending WA interactive: {e}")

    def process_read_receipt(self, status):
        """
        Processes WhatsApp read receipts and syncs the read status to the real-time chat dashboard.
        """
        phone_number = status.get('recipient_id')
        if not phone_number:
            return
            
        from django.core.cache import cache
        from core_auth.models import User
        from chat.models import Conversation, Message
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        
        # Determine the client logic
        users = User.objects.filter(role=User.CLIENT)
        client = None
        for u in users:
            if u.phone_number and u.phone_number.replace('+', '').replace(' ', '')[-10:] == phone_number[-10:]:
                client = u
                break
                
        if not client:
            return
            
        session_key = f"wa_session_{client.id}"
        active_conv_id = cache.get(session_key)
        
        # If there's an active session, mark their messages as read
        if active_conv_id:
            try:
                conv = Conversation.objects.get(id=active_conv_id)
                # We mark any unread messages sent BY the consultant AS READ 
                # because the client's WhatsApp just sent a read receipt.
                updated_count = Message.objects.filter(
                    conversation=conv, 
                    sender=conv.consultant, 
                    is_read=False
                ).update(is_read=True)
                
                if updated_count > 0:
                    logger.info(f"Marked {updated_count} messages read for WA client {phone_number}")
                    # Broadcast the read receipt to the consultant's dashboard instantly
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'chat_{conv.id}',
                        {
                            'type': 'messages_read',
                            'reader_id': client.id,
                            'reader_username': client.username,
                        }
                    )
            except Exception as e:
                logger.error(f"Error syncing WA read receipt: {e}")
