"""
WebSocket consumer for real-time chat functionality.
Industrial-grade implementation with proper logging, error handling,
presence tracking, and read receipts.
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import transaction

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Async WebSocket consumer for chat with presence tracking.
    
    Connection URL: ws://.../ws/chat/{conversation_id}/?token=<jwt_token>
    
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
        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        self.room_group_name = f'chat_{self.conversation_id}'
        self.user = self.scope.get('user')
        
        logger.info(f"WebSocket connect attempt: user={getattr(self.user, 'username', 'anonymous')}, conversation={self.conversation_id}")
        
        # Reject if not authenticated
        if isinstance(self.user, AnonymousUser) or not self.user.is_authenticated:
            logger.warning(f"WebSocket rejected: unauthenticated user for conversation {self.conversation_id}")
            await self.close(code=4001)
            return
        
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
        logger.info(f"WebSocket accepted: user={self.user.username}, conversation={self.conversation_id}")
        
        # Broadcast presence: user joined with a request for others to reply
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_presence',
                'user_id': self.user.id,
                'username': self.user.username,
                'status': 'online',
                'request_reply': True,  # Ask others to let us know they are here
            }
        )
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection with presence broadcast."""
        logger.info(f"WebSocket disconnect: user={getattr(self.user, 'username', 'unknown')}, code={close_code}")
        
        if hasattr(self, 'room_group_name') and hasattr(self, 'user') and self.user.is_authenticated:
            # Broadcast presence: user left
            try:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_presence',
                        'user_id': self.user.id,
                        'username': self.user.username,
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
            
            # Broadcast to room group
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
                    'temp_id': temp_id,  # Echo back for client matching
                }
            )
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
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_indicator',
                'user_id': self.user.id,
                'username': self.user.username,
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
            'temp_id': event.get('temp_id'),
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
    def check_participant(self):
        """Check if user is a participant of the conversation."""
        from .models import Conversation
        
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            return self.user.id in [conversation.consultant_id, conversation.client_id]
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
        
        print(f"[CHAT] save_message called: conv={self.conversation_id}, user={self.user.id}")  # DEBUG
        
        try:
            with transaction.atomic():
                conversation = Conversation.objects.select_for_update().get(id=self.conversation_id)
                print(f"[CHAT] Found conversation: {conversation.id}")  # DEBUG
                
                message = Message.objects.create(
                    conversation=conversation,
                    sender=self.user,
                    content=content
                )
                print(f"[CHAT] Created message: {message.id}")  # DEBUG
                
                # Update conversation timestamp
                conversation.save(update_fields=['updated_at'])
                
                return {
                    'id': message.id,
                    'sender_id': self.user.id,
                    'sender_username': self.user.username,
                    'content': message.content,
                    'timestamp': message.timestamp.isoformat(),
                    'is_read': message.is_read,
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
