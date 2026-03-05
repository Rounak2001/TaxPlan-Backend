import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        print(f"[WS NOTIF] Connecting attempt for user: {self.user}")
        
        if not self.user or not self.user.is_authenticated:
            print("[WS NOTIF] Connection rejected: User not authenticated")
            await self.close()
            return

        self.group_name = f"user_{self.user.id}"

        # Join room group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()
        print(f"[WS NOTIF] Connection accepted for user: {self.user.username} (Group: {self.group_name})")

    async def disconnect(self, close_code):
        # Leave room group
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    # Receive message from room group
    async def notification_message(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps(event["data"]))
