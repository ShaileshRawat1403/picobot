---
name: whatsapp
description: "Send and receive WhatsApp messages. Chat with individuals and groups, send media, and manage conversations."
metadata: {"picobot":{"emoji":"💬","requires":{"bins":["npm"]},"install":[{"id":"bridge","kind":"script","description":"Install WhatsApp bridge via 'picobot channels login'"}]}}
---

# WhatsApp Skill

Use this skill to send and receive WhatsApp messages. You can chat with individuals and groups, send media, and manage your WhatsApp conversations through picobot.

## Setup

Before using this skill, you need to set up the WhatsApp bridge:

1. Ensure you have Node.js >= 18 and npm installed
2. Run: `picobot channels login`
3. Scan the QR code with your WhatsApp mobile app
4. Once connected, you can start sending and receiving messages

## Sending Messages

To send a WhatsApp message:
```bash
# Send a text message
whatsapp send --to "+1234567890" --message "Hello from picobot!"

# Send a message to a group
whatsapp send --to "12036301234567890@g.us" --message "Hello team!"

# Send an image
whatsapp send --to "+1234567890" --message "Check this out!" --media "/path/to/image.jpg"

# Send a document
whatsapp send --to "+1234567890" --message "Please review this document" --media "/path/to/document.pdf"
```

## Receiving Messages

Messages received via WhatsApp will automatically appear in your picobot conversation. You can:
- Respond directly to incoming messages
- Use picobot's AI capabilities to analyze and respond to messages
- Forward messages to other channels or skills

## Managing Conversations

To manage your WhatsApp conversations:
```bash
# List recent conversations
whatsapp chats list --limit 10

# Get conversation history with a contact
whatsapp chats history --contact "+1234567890" --limit 50

# Clear conversation history (local only)
whatsapp chats clear --contact "+1234567890"
```

## Media Handling

When you receive media files via WhatsApp:
- Images, videos, and documents are automatically downloaded
- You can access them using picobot's file tools
- Media paths are provided in message metadata
- You can forward media to other contacts or save them locally

## Group Management

To manage WhatsApp groups:
```bash
# Create a new group
whatsapp group create --name "Project Team" --participants "+1234567890,+0987654321"

# Add participants to a group
whatsapp group add --group-id "12036301234567890@g.us" --participants "+1122334455"

# Remove participants from a group
whatsapp group remove --group-id "12036301234567890@g.us" --participants "+1122334455"

# Get group info
whatsapp group info --group-id "12036301234567890@g.us"
```

## Status and Presence

To check your WhatsApp connection status:
```bash
# Check connection status
whatsapp status

# Get your profile info
whatsapp profile

# Update your status message
whatsapp status set --message "Available for work"
```

## Integration with Other Skills

You can combine WhatsApp with other skills for powerful workflows:
```bash
# Example: Get email summary and send via WhatsApp
picobot agent -m "Summarize my unread emails from today" | whatsapp send --to "+1234567890" --message

# Example: Create task from WhatsApp message
# (When you receive a message, you can respond with task creation commands)

# Example: Send calendar events via WhatsApp
picobot agent -m "List my meetings for tomorrow" | whatsapp send --to "+1234567890" --message
```

## Troubleshooting

If you encounter issues:
1. Ensure the bridge is running: `ps aux | grep npm`
2. Check connection status: `whatsapp status`
3. Restart the bridge if needed: Stop and start picobot
4. Re-scan QR code: Run `picobot channels login` again
5. Check logs for error messages

## Security Notes

- Your WhatsApp authentication data is stored locally in `~/.picobot/runtime/whatsapp-auth/`
- The bridge runs locally on your machine - no data leaves your device except through WhatsApp's normal channels
- Be cautious when sharing sensitive information through any messaging platform