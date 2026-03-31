---
name: task
description: "Manage tasks and projects. Create, update, track, and organize tasks. Supports Trello, Asana, Jira, and Todoist."
metadata: {"picobot":{"emoji":"✅","requires":{"bins":[]},"install":[]}}
---

# Task Management Skill

Use this skill to manage tasks and projects. You can create, update, track, and organize tasks across different platforms.

## Creating Tasks

To create a new task:
```bash
# Create a basic task
task create --title "Finish project proposal" --description "Complete the Q1 project proposal document"

# Create a task with due date and priority
task create --title "Review code changes" --description "Review pull request #123" --due "2024-01-20" --priority "high"

# Create a task in a specific project/list
task create --title "Update documentation" --project "Website Redesign" --column "In Progress"

# Create a recurring task (daily standup)
task create --title "Daily standup" --description "Team daily standup meeting" --recurrence "daily" --time "09:00"

# Create a task with labels/tags
task create --title "Fix login bug" --labels "bug,frontend,urgent" --assignee "john.doe"
```

## Viewing Tasks

To view tasks:
```bash
# View all tasks assigned to you
task list --assignee "me"

# View tasks in a specific project
task list --project "Website Redesign"

# View tasks with specific status
task list --status "in-progress"

# View tasks due today
task list --due today

# View tasks due this week
task list --due week

# View overdue tasks
task list --overdue

# View tasks with specific labels
task list --labels "urgent,frontend"

# Search tasks by keyword
task search --query "database migration"

# Get details of a specific task
task show --id "task_123"
```

## Updating Tasks

To update tasks:
```bash
# Update task status
task update --id "task_123" --status "done"

# Update task priority
task update --id "task_123" --priority "high"

# Update task due date
task update --id "task_123" --due "2024-01-25"

# Add comment to task
task comment --id "task_123" --comment "Made progress on the frontend implementation"

# Assign task to someone
task update --id "task_123" --assignee "jane.smith"

# Add labels to task
task label --id "task_123" --add "review needed"

# Remove labels from task
task label --id "task_123" --remove "in progress"
```

## Managing Projects

To manage projects and boards:
```bash
# List all projects/boards
task projects list

# Create a new project
task projects create --name "Mobile App Redesign" --description "Redesign the mobile application interface"

# Get project details
task projects show --id "proj_456"

# Update project details
task projects update --id "proj_456" --description "Updated project description"

# Archive/complete a project
task projects archive --id "proj_456"
```

## Time Tracking

To track time spent on tasks:
```bash
# Start time tracking on a task
task time start --id "task_123"

# Stop time tracking
task time stop --id "task_123"

# Log time spent on a task
task time log --id "task_123" --hours 2.5 --description "Worked on authentication module"

# View time reports for a task
task time report --id "task_123"

# View time reports for a project
task time report --project "Website Redesign"
```

## Task Templates

To use task templates:
```bash
# List available task templates
task templates list

# Create a task from template
task create --template "bug-report" --title "Login fails on Safari"

# Create a new task template
task templates create --name "feature-request" --description "Template for feature requests" --fields "title,description,priority,labels"
```