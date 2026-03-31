"""Google Calendar tool for Picobot."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from picobot.agent.tools.base import Tool


class CalendarTool(Tool):
    """Read Google Calendar events."""
    
    name = "calendar"
    description = "View upcoming calendar events. Use this to check schedules, meetings, and reminders."
    parameters = {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "Number of days to look ahead"},
            "query": {"type": "string", "description": "Search for events matching this text"},
        },
        "required": [],
    }

    async def execute(self, days: int = 7, query: str | None = None, **kwargs: Any) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            TOKEN_FILE = os.path.expanduser('~/.picobot/runtime/google-calendar-token.json')
            
            if not os.path.exists(TOKEN_FILE):
                return "Error: Calendar not configured. Run OAuth setup first."
            
            creds = Credentials.from_authorized_user_file(
                TOKEN_FILE, 
                ['https://www.googleapis.com/auth/calendar.readonly']
            )
            if creds.expired:
                creds.refresh(Request())
            
            service = build('calendar', 'v3', credentials=creds)
            
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days)
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy='startTime',
                q=query
            ).execute()
            
            events = events_result.get('items', [])
            
            if not events:
                return f"No events found in the next {days} days."
            
            lines = [f"📅 Calendar - Next {days} days:\n"]
            for e in events:
                start = e['start'].get('dateTime', e['start'].get('date'))
                title = e.get('summary', 'No title')
                loc = e.get('location', '')
                
                date_str = start[:10]
                time_str = start[11:16] if 'T' in start else 'All day'
                
                lines.append(f"• {date_str} {time_str} - {title}")
                if loc:
                    lines.append(f"  📍 {loc}")
            
            return "\n".join(lines)
            
        except Exception as ex:
            return f"Calendar error: {str(ex)}"
