
import logging
import requests
import json

class DiscordWebhookHandler(logging.Handler):
    def __init__(self, webhook_url, username="RDB Logger", avatar_url=None):
        super().__init__()
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url  # Optional avatar URL (can be an image URL)
 
    def emit(self, record):
        # Format the log message
        log_message = self.format(record)
       
        # Define the embed structure
        embed = {
            "title": "Error Notification",
            "description": log_message,  # Include the formatted log message in the embed
            "color": 16711680  # Color code for red (for errors)
        }
 
        # Build the payload
        payload = {
            "username": self.username,  # Custom username for the webhook message
            "avatar_url": self.avatar_url,  # Custom avatar URL (optional)
            "embeds": [embed]  # Embed content for richer display
        }
 
        # Define the headers for the request
        headers = {
            "Content-Type": "application/json"
        }
 
        # Send the request to the Discord webhook URL
        try:
            response = requests.post(self.webhook_url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()  # Raise an error if the request failed
        except requests.exceptions.RequestException as e:
            print(f"Error sending log to Discord: {e}")
