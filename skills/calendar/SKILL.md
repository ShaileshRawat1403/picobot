---
name: calendar
description: "Manage calendar events and schedules. Create, view, update, and delete events. Supports Google Calendar and Outlook."
metadata: {"picobot":{"emoji":"📅","requires":{"bins":[]},"install":[]}}
---

# Calendar Skill

Use this skill to manage calendar events and schedules. You can create, view, update, and delete calendar events.

## Creating Events

To create a new calendar event:
```bash
# Create a basic event
cal create --title "Team Meeting" --start "2024-01-15 14:00" --end "2024-01-15 15:00"

# Create an event with description and location
cal create --title "Doctor Appointment" --start "2024-01-20 10:30" --end "2024-01-20 11:30" --description "Annual checkup" --location "Medical Center"

# Create an all-day event
cal create --title "Vacation Day" --start "2024-02-01" --all-day true

# Create a recurring event (weekly team meeting)
cal create --title "Weekly Sync" --start "2024-01-16 09:00" --end "2024-01-16 10:00" --recurrence "weekly" --count 4
```

## Viewing Events

To view calendar events:
```bash
# View today's events
cal list --today

# View this week's events
cal list --week

# View events for a specific date
cal list --date "2024-01-20"

# View events in a date range
cal list --start "2024-01-15" --end "2024-01-22"

# Search for events containing specific text
cal list --search "meeting"

# View detailed information about an event
cal show --id "abc123"
```

## Updating Events

To update an existing calendar event:
```bash
# Update event time
cal update --id "abc123" --start "2024-01-15 15:00" --end "2024-01-15 16:00"

# Update event title and description
cal update --id "abc123" --title "Updated Meeting Title" --description "Discuss project timeline"

# Mark an event as completed
cal update --id "abc123" --status "completed"
```

## Deleting Events

To delete a calendar event:
```bash
# Delete an event
cal delete --id "abc123"

# Delete multiple events by ID
cal delete --id "abc123" --id "def456" --id "ghi789"

# Delete all events matching a search
cal delete --search "old meeting"
```

## Calendar Management

To manage your calendars:
```bash
# List all available calendars
cal calendars list

# Create a new calendar
cal calendars create --name "Project Alpha" --description "Tasks for Project Alpha"

# Delete a calendar
cal calendars delete --id "cal_123"
```

## Time Zones

All commands support time zone specification:
```bash
# Specify time zone for events
cal create --title "International Call" --start "2024-01-20 09:00" --end "2024-01-20 10:00" --time-zone "America/New_York"

# View events in a specific time zone
cal list --date "2024-01-20" --time-zone "Europe/London"
```