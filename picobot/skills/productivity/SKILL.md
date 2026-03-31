---
name: productivity
description: "Unified productivity workflows combining calendar, email, and task management. Streamline common work processes like meeting preparation, daily planning, and project tracking."
metadata: {"picobot":{"emoji":"🚀","requires":{"bins":[]},"install":[]}}
---

# Productivity Skill

Use this skill to streamline common productivity workflows by combining calendar, email, and task management capabilities.

## Meeting Preparation

Prepare for upcoming meetings by gathering relevant information:
```bash
# Prepare for meetings today
productivity meeting-prep --today

# Prepare for meetings this week
productivity meeting-prep --week

# Prepare for a specific meeting (by ID or title)
productivity meeting-prep --meeting "Project Alpha Review"

# Prepare for meetings containing specific keyword
productivity meeting-prep --search "budget"
```

The meeting preparation workflow will:
1. Check your calendar for upcoming meetings
2. Find recent emails related to meeting participants/topics
3. Identify action items from previous meetings
4. Gather relevant documents and notes
5. Provide a briefing document

## Daily Planning

Plan your day with a unified view of commitments:
```bash
# Plan for today
productivity daily-plan --today

# Plan for tomorrow
productivity daily-plan --tomorrow

# Plan for a specific date
productivity daily-plan --date "2024-01-20"
```

The daily planning workflow will:
1. Show your schedule for the day
2. Highlight overdue tasks
3. Show tasks due today
4. Display unread/important emails requiring attention
5. Suggest time blocks for focused work

## Project Tracking

Track project progress across tools:
```bash
# Get status of a project
productivity project-status --name "Website Redesign"

# List all active projects
productivity project-list

# Update project status from recent activity
productivity project-update --name "Mobile App" --source "recent"
```

The project tracking workflow will:
1. Gather tasks related to the project from task management tools
2. Check calendar for project meetings and milestones
3. Find emails related to the project
4. Provide a progress summary
5. Identify blockers and upcoming deadlines

## Action Item Management

Capture and follow up on action items from various sources:
```bash
# Extract action items from recent meetings
productivity action-items --source "meetings" --days 7

# Extract action items from emails
productivity action-items --source "email" --days 3

# Extract action items from Slack/Teams (if integrated)
productivity action-items --source "chat" --days 1

# Mark action items as completed
productivity action-items --complete --id "action_123" --id "action_456"
```

The action item management workflow will:
1. Scan specified sources for action items (using keywords like "action", "todo", "follow up")
2. Create tasks in your task management system
3. Set due dates and assignees when mentioned
4. Provide a centralized list of action items
5. Track completion status

## Weekly Review

Conduct a weekly review of your productivity:
```bash
# Conduct weekly review
productivity weekly-review --last-week

# Conduct review for specific week
productivity weekly-review --start "2024-01-15" --end "2024-01-21"
```

The weekly review workflow will:
1. Review completed tasks and achievements
2. Analyze time spent in meetings vs. focused work
3. Review email response times and inbox zero progress
4. Identify patterns and productivity trends
5. Set goals for the upcoming week
6. Suggest process improvements

## Custom Workflows

Create custom productivity workflows by combining commands:
```bash
# Example: Prepare for meeting and create follow-up tasks
productivity meeting-prep --meeting "Sprint Planning" | productivity action-items --source "meeting"

# Example: Daily planning with email triage
productivity daily-plan --today && productivity email triage --unread --limit 20
```

## Integration Points

This skill works best when the following skills are configured:
- `calendar`: For schedule management
- `email`: For communication management  
- `task`: For task and project management

Ensure these skills are set up with appropriate authentication for full functionality.