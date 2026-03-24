"""
WebSocket consumer for real-time chat functionality.
Industrial-grade implementation with proper logging, error handling,
presence tracking, and read receipts.
"""

import json
import logging
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Async WebSocket consumer for chat with presence tracking.
    
    Connection URL: ws://.../ws/chat/{conversation_id}/?token=<jwt_token>[&profile_id=<sub_account_id>]
    
    The optional `profile_id` query param tells the consumer which sub-account
    is the active sender. If omitted, the main (JWT) account is used.
    
    Inbound Message Types:
    - {"type": "message", "content": "Hello!"}  - Send a message
    - {"type": "mark_read"}                      - Mark messages as read
    - {"type": "typing", "is_typing": true}      - Typing indicator
    
    Outbound Message Types:
    - {"type": "message", ...}           - New message
    - {"type": "presence", ...}          - User online/offline
    - {"type": "read_receipt", ...}      - Messages read
    - {"type": "typing", ...}            - User typing
    - {"type": "error", ...}             - Error occurred
    - {"type": "ack", ...}               - Message acknowledged
    """
    
    async def connect(self):
        """Handle WebSocket connection with presence broadcast."""
        try:
            self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
            self.room_group_name = f'chat_{self.conversation_id}'
            self.user = self.scope.get('user')
            
            logger.info(f"WebSocket connect attempt: user={getattr(self.user, 'username', 'anonymous')}, conversation={self.conversation_id}")
            
            # Reject if not authenticated
            if isinstance(self.user, AnonymousUser) or not self.user.is_authenticated:
                logger.warning(f"WebSocket rejected: unauthenticated user for conversation {self.conversation_id}")
                await self.close(code=4001)
                return
            
            # Resolve effective sender: if a valid sub-account profile_id is passed,
            # messages will be attributed to that sub-account user.
            query_string = self.scope.get('query_string', b'').decode()
            query_params = parse_qs(query_string)
            profile_id_list = query_params.get('profile_id', [])
            self.effective_sender = await self.resolve_effective_sender(
                profile_id_list[0] if profile_id_list else None
            )
            logger.info(f"Effective sender resolved: {self.effective_sender.username} (id={self.effective_sender.id})")

            # Validate user is a participant
            is_participant = await self.check_participant()
            if not is_participant:
                logger.warning(f"WebSocket rejected: user {self.user.username} not participant in {self.conversation_id}")
                await self.close(code=4003)
                return
            
            # Join room group
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            
            await self.accept()
            logger.info(f"WebSocket accepted: user={self.user.username}, effective_sender={self.effective_sender.username}, conversation={self.conversation_id}")
            
            # Broadcast presence: user joined with a request for others to reply
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_presence',
                    'user_id': self.effective_sender.id,
                    'username': self.effective_sender.username,
                    'status': 'online',
                    'request_reply': True,
                }
            )
        except Exception as e:
            logger.exception(f"Error in ChatConsumer.connect: {e}")
            await self.close()
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection with presence broadcast."""
        effective = getattr(self, 'effective_sender', None) or getattr(self, 'user', None)
        logger.info(f"WebSocket disconnect: user={getattr(self.user, 'username', 'unknown')}, code={close_code}")
        
        if hasattr(self, 'room_group_name') and hasattr(self, 'user') and self.user.is_authenticated:
            # Broadcast presence: user left
            try:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_presence',
                        'user_id': effective.id if effective else self.user.id,
                        'username': effective.username if effective else self.user.username,
                        'status': 'offline',
                    }
                )
            except Exception as e:
                logger.error(f"Error broadcasting disconnect: {e}")
            
            # Leave room group
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
    
    async def receive(self, text_data):
        """Handle incoming messages from WebSocket."""
        print(f"[CHAT] Receive called: {text_data[:100]}")  # DEBUG
        try:
            data = json.loads(text_data)
            msg_type = data.get('type', 'message')
            
            print(f"[CHAT] Parsed type={msg_type}, data keys={data.keys()}")  # DEBUG
            logger.debug(f"Received {msg_type} from {self.user.username}: {data}")
            
            if msg_type == 'mark_read':
                await self.handle_mark_read()
            elif msg_type == 'typing':
                await self.handle_typing(data.get('is_typing', False))
            elif msg_type == 'message' or 'content' in data:
                print(f"[CHAT] Calling handle_message with content: {data.get('content', '')[:50]}")  # DEBUG
                await self.handle_message(data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            print(f"[CHAT] JSON decode error: {e}")  # DEBUG
            logger.error(f"Invalid JSON from {self.user.username}: {e}")
            await self.send_error("Invalid message format")
        except Exception as e:
            print(f"[CHAT] Exception in receive: {e}")  # DEBUG
            logger.exception(f"Error processing message from {self.user.username}: {e}")
            await self.send_error("Internal error")
    
    async def handle_message(self, data):
        """Handle sending a new chat message."""
        content = data.get('content', '').strip()
        temp_id = data.get('tempId')  # For matching with optimistic update
        
        if not content:
            await self.send_error("Empty message")
            return
        
        # Save message to database
        message = await self.save_message(content)
        
        if message:
            print(f"[CHAT] Message saved to DB: id={message['id']}")  # DEBUG
            logger.info(f"Message saved: id={message['id']}, sender={self.user.username}")
            
            # Broadcast to room group (for the chat window)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'id': message['id'],
                    'sender_id': message['sender_id'],
                    'sender_username': message['sender_username'],
                    'content': message['content'],
                    'timestamp': message['timestamp'],
                    'is_read': message['is_read'],
                    'delivery_channel': message.get('delivery_channel', 'dashboard'),
                    'temp_id': temp_id,
                }
            )

            # Send real-time notification to the recipient's dashboard (if they are NOT in the chat room)
            if self.user.role == 'CONSULTANT':
                recipient_id = message.get('client_id')
                if recipient_id:
                    await self.channel_layer.group_send(
                        f"user_{recipient_id}",
                        {
                            "type": "notification_message",
                            "data": {
                                "type": "NEW_MESSAGE",
                                "category": "chat",
                                "title": f"New message from {self.user.first_name or self.user.username}",
                                "message": message['content'][:50] + ('...' if len(message['content']) > 50 else ''),
                                "conversation_id": str(self.conversation_id),
                                "sender_name": self.user.first_name or self.user.username,
                            }
                        }
                    )
                    logger.info(f"Sent real-time notification to client {recipient_id}")
        else:
            print(f"[CHAT] FAILED to save message")  # DEBUG
            logger.error(f"Failed to save message from {self.user.username}")
            await self.send_error("Failed to save message")
    
    async def handle_mark_read(self):
        """Handle marking messages as read."""
        read_count = await self.mark_messages_read()
        logger.debug(f"Marked {read_count} messages as read for {self.user.username}")
        
        if read_count > 0:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'messages_read',
                    'reader_id': self.user.id,
                    'reader_username': self.user.username,
                }
            )
    
    async def handle_typing(self, is_typing):
        """Handle typing indicator."""
        effective = getattr(self, 'effective_sender', self.user)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_indicator',
                'user_id': effective.id,
                'username': effective.username,
                'is_typing': is_typing,
            }
        )
    
    async def send_error(self, message):
        """Send error message to client."""
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': message,
        }))
    
    # ===== Broadcast Handlers =====
    
    async def chat_message(self, event):
        """Broadcast: New message."""
        print(f"[CHAT] Broadcasting message to {self.user.username}: id={event['id']}")  # DEBUG
        await self.send(text_data=json.dumps({
            'type': 'message',
            'id': event['id'],
            'sender_id': event['sender_id'],
            'sender_username': event['sender_username'],
            'content': event['content'],
            'timestamp': event['timestamp'],
            'is_read': event['is_read'],
            'delivery_channel': event.get('delivery_channel', 'dashboard'),
            'temp_id': event.get('temp_id'),
        }))
    
    async def delivery_status(self, event):
        """Broadcast: WhatsApp delivery status update from Celery."""
        await self.send(text_data=json.dumps({
            'type': 'delivery_status',
            'message_id': event['message_id'],
            'delivery_channel': event['delivery_channel'],
        }))
    
    async def user_presence(self, event):
        """Broadcast: User presence change."""
        print(f"[CHAT] Broadcasting presence to {self.user.username}: {event['username']} is {event['status']}")  # DEBUG
        await self.send(text_data=json.dumps({
            'type': 'presence',
            'user_id': event['user_id'],
            'username': event['username'],
            'status': event['status'],
        }))
        
        # If someone else joined (status='online' and request_reply=True), 
        # let them know we are also online
        if (event.get('request_reply') is True and 
            event['status'] == 'online' and 
            event['user_id'] != self.user.id):
            
            logger.debug(f"Replying to presence request from {event['username']} for {self.user.username}")
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_presence',
                    'user_id': self.user.id,
                    'username': self.user.username,
                    'status': 'online',
                    'request_reply': False, # Don't request again to avoid loops
                }
            )
    
    async def messages_read(self, event):
        """Broadcast: Read receipt."""
        print(f"[CHAT] Broadcasting read_receipt to {self.user.username}")  # DEBUG
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'reader_id': event['reader_id'],
            'reader_username': event['reader_username'],
        }))
    
    async def typing_indicator(self, event):
        """Broadcast: Typing indicator."""
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'user_id': event['user_id'],
            'username': event['username'],
            'is_typing': event['is_typing'],
        }))
    
    # ===== Database Operations =====
    
    @database_sync_to_async
    def resolve_effective_sender(self, profile_id):
        """
        Resolve the effective sender from the optional profile_id param.
        
        - If profile_id is provided and the profile is a sub-account of self.user → return sub-account
        - Otherwise → return self.user (main account)
        """
        if not profile_id:
            return self.user
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            profile = User.objects.get(id=profile_id)
            # Security: only allow if it's a direct sub-account of the JWT user
            if profile.parent_account_id == self.user.id:
                return profile
        except Exception:
            pass
        return self.user

    @database_sync_to_async
    def check_participant(self):
        """Check if user (or their sub-account) is a participant of the conversation."""
        from .models import Conversation
        
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            # Accept if JWT user is consultant or client directly
            if self.user.id in [conversation.consultant_id, conversation.client_id]:
                return True
            # Accept if JWT user is the parent of the conversation's client (sub-account chat)
            if conversation.client.parent_account_id == self.user.id:
                return True
            return False
        except Conversation.DoesNotExist:
            logger.warning(f"Conversation not found: {self.conversation_id}")
            return False
        except Exception as e:
            logger.exception(f"Error checking participant: {e}")
            return False
    
    @database_sync_to_async
    def save_message(self, content):
        """Save a message to database with proper transaction handling."""
        from .models import Conversation, Message
        
        # Use the effective sender (resolved sub-account or main user)
        sender = getattr(self, 'effective_sender', self.user)
        print(f"[CHAT] save_message called: conv={self.conversation_id}, effective_sender={sender.id}")  # DEBUG
        
        try:
            with transaction.atomic():
                conversation = Conversation.objects.select_for_update().get(id=self.conversation_id)
                print(f"[CHAT] Found conversation: {conversation.id}")  # DEBUG
                
                message = Message.objects.create(
                    conversation=conversation,
                    sender=sender,
                    content=content
                )
                print(f"[CHAT] Created message: {message.id}")  # DEBUG
                
                # Update conversation timestamp
                conversation.save(update_fields=['updated_at'])
                # Build WhatsApp message with clear sender + recipient context
                consultant_name = self.user.first_name or self.user.username

                # If chatting with a sub-account, prefix with their name so the
                # main account holder (who gets the WA notification) knows who it's for.
                client = conversation.client
                is_sub_account = bool(client.parent_account_id)
                if is_sub_account:
                    member_name = client.first_name or client.username
                    wa_message = f"[For: {member_name}]\n{consultant_name}: {content}"
                else:
                    wa_message = f"{consultant_name}: {content}"

                
                # --- WhatsApp Outbound Sync & Session Locking ---
                if self.user.role == 'CONSULTANT':
                    # Lock the client's WhatsApp reply to THIS consultant/conversation for 24 hours
                    session_key = f"wa_session_{conversation.client.id}"
                    cache.set(session_key, conversation.id, 86400)
                    logger.info(f"Locked WhatsApp session for client {conversation.client.id} to conversation {conversation.id}")

                    # If the client is a sub-account with no phone, fall back to the
                    # main (parent) account's verified phone number.
                    notify_phone = conversation.client.phone_number
                    if not notify_phone and conversation.client.parent_account_id:
                        notify_phone = conversation.client.parent_account.phone_number

                    if notify_phone:
                        # Send message to client's WhatsApp securely via Meta API using Celery
                        from notifications.tasks import send_whatsapp_text_task
                        
                        # Ensure phone number is formatted correctly
                        client_phone = notify_phone.replace('+', '').replace(' ', '')
                        
                        # Offload to Celery background task with message_id for status tracking
                        send_whatsapp_text_task.delay(
                            phone_number=client_phone,
                            text=wa_message,
                            message_id=message.id
                        )
                        logger.info(f"Queued outbound WhatsApp message to {client_phone[-4:]} via Celery")
                    else:
                        logger.warning(f"No phone number for client {conversation.client.id} or their main account — WhatsApp skipped")
                # ------------------------------


                return {
                    'id': message.id,
                    'sender_id': self.user.id,
                    'sender_username': self.user.username,
                    'content': message.content,
                    'timestamp': message.timestamp.isoformat(),
                    'is_read': message.is_read,
                    'delivery_channel': message.delivery_channel,
                    'client_id': conversation.client.id,  # For dashboard notifications
                }
        except Conversation.DoesNotExist:
            print(f"[CHAT] ERROR: Conversation not found: {self.conversation_id}")  # DEBUG
            logger.error(f"Conversation not found when saving: {self.conversation_id}")
            return None
        except Exception as e:
            print(f"[CHAT] ERROR saving message: {e}")  # DEBUG
            logger.exception(f"Error saving message: {e}")
            return None
    
    @database_sync_to_async
    def mark_messages_read(self):
        """Mark messages from other user as read."""
        from .models import Conversation, Message
        
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            updated = Message.objects.filter(
                conversation=conversation,
                is_read=False
            ).exclude(sender=self.user).update(is_read=True)
            return updated
        except Exception as e:
            logger.exception(f"Error marking messages read: {e}")
            return 0
