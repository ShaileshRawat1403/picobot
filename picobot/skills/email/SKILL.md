---
name: email
description: "Manage email communications. Read, send, search, and organize emails. Supports Gmail and Outlook."
metadata: {"picobot":{"emoji":"📧","requires":{"bins":[]},"install":[]}}
---

# Email Skill

Use this skill to manage email communications. You can read, send, search, and organize emails.

## Sending Emails

To send an email:
```bash
# Send a simple email
email send --to "recipient@example.com" --subject "Meeting Tomorrow" --body "See you at 2pm in conference room B."

# Send an email with CC and BCC
email send --to "recipient@example.com" --cc "manager@example.com" --bcc "archive@example.com" --subject "Project Update" --body "Here's the latest status..."

# Send an HTML formatted email
email send --to "recipient@example.com" --subject "Newsletter" --body "<h1>Monthly Newsletter</h1><p>Welcome to our update!</p>" --html true

# Send an email with attachment
email send --to "recipient@example.com" --subject "Report" --body "Please find the attached report." --attachment "/path/to/report.pdf"
```

## Reading Emails

To read emails:
```bash
# Read unread emails in inbox
email read --unread --limit 10

# Read emails from a specific sender
email read --from "sender@example.com" --limit 5

# Read emails with a specific subject
email read --subject "Meeting" --limit 5

# Read emails in a specific folder/label
email read --folder "Work" --limit 10

# Read emails containing specific text
email read --search "project deadline" --limit 10

# Read a specific email by ID
email read --id "abc123"

# Mark emails as read
email read --id "abc123" --mark-read
```

## Searching Emails

To search for emails:
```bash
# Search emails by keyword
email search --query "invoice" --limit 20

# Search emails by date range
email search --query "meeting" --start-date "2024-01-01" --end-date "2024-01-31"

# Search emails with attachments
email search --query "report" --has-attachment true

# Search emails from specific sender
email search --query "" --from "boss@company.com"

# Search emails to specific recipient
email search --query "" --to "team@company.com"
```

## Managing Emails

To manage emails (move, label, delete):
```bash
# Move email to folder/label
email move --id "abc123" --folder "Archive"

# Apply label to email
email label --id "abc123" --label "Important"

# Remove label from email
email unlabel --id "abc123" --label "Important"

# Delete email (move to trash)
email delete --id "abc123"

# Permanently delete email (skip trash)
email delete --id "abc123" --permanent

# Mark email as spam
email spam --id "abc123"

# Mark email as not spam
email notspam --id "abc123"
```

## Draft Emails

To work with draft emails:
```bash
# Create a draft
email draft create --to "recipient@example.com" --subject "Draft Subject" --body "Draft body content"

# List drafts
email draft list --limit 10

# Update a draft
email draft update --id "draft123" --body "Updated body content"

# Send a draft
email draft send --id "draft123"

# Delete a draft
email draft delete --id "draft123"
```

## Email Settings

To configure email settings:
```bash
# Set default signature
email signature set --text "Best regards,\nJohn Doe\njohn@example.com"

# Get current signature
email signature get

# Set out of office autoreply
email autoreply set --start "2024-06-01" --end "2024-06-15" --message "I am out of office until June 15."

# Disable out of office autoreply
email autoreply clear
```