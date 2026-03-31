#!/bin/bash
# Outlook/Office 365 Calendar authentication script
# This script helps users authenticate with Microsoft Graph API

echo "Setting up Outlook/Office 365 Calendar authentication..."
echo "Please follow these steps:"
echo "1. Go to https://portal.azure.com/"
echo "2. Azure Active Directory -> App registrations -> New registration"
echo "3. Enter application name and redirect URI (can be http://localhost for testing)"
echo "4. Under 'API permissions', add 'Calendars.ReadWrite' delegated permission"
echo "5. Click 'Grant admin consent'"
echo "6. Go to 'Certificates & secrets' -> New client secret"
echo "7. Copy the client secret value (you won't see it again)"
echo ""
echo "After obtaining the following information:"
echo "   - Application (client) ID"
echo "   - Directory (tenant) ID"  
echo "   - Client secret value"
echo ""
echo "Create a configuration file at:"
echo "   ~/.picobot/runtime/outlook-calendar-config.json"
echo ""
echo "With this content:"
echo '{
  "client_id": "YOUR_APP_CLIENT_ID",
  "tenant_id": "YOUR_TENANT_ID", 
  "client_secret": "YOUR_CLIENT_SECRET"
}'
echo ""
echo "Then run this command to complete setup:"
echo "   python3 -m picobot skill auth outlook-calendar"