#!/bin/bash
# Google Calendar authentication script
# This script helps users authenticate with Google Calendar API

echo "Setting up Google Calendar authentication..."
echo "Please follow these steps:"
echo "1. Go to https://console.cloud.google.com/"
echo "2. Create a new project or select existing project"
echo "3. Enable Google Calendar API"
echo "4. Create OAuth 2.0 Client ID credentials"
echo "5. Download the credentials JSON file"
echo ""
echo "After downloading credentials.json, place it in:"
echo "   ~/.picobot/runtime/google-calendar-credentials.json"
echo ""
echo "Then run this command to complete setup:"
echo "   python3 -m picobot skill auth google-calendar"