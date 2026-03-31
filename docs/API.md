# Picobot API Reference

## Overview

Picobot exposes REST endpoints for integration with external systems like Soothsayer. All endpoints are available at `http://localhost:3000/api/picobot`.

## Authentication

Protected endpoints require JWT Bearer token:
```
Authorization: Bearer <token>
```

## Endpoints

### Health Check

```
POST /api/picobot/webhook/health
```

Public endpoint for Picobot to report health status.

**Request:**
```json
{
  "status": "online",
  "uptime": {
    "seconds": 3600,
    "version": "2.0.0"
  }
}
```

---

### Activity Sync

```
POST /api/picobot/webhook/activity
```

Public endpoint for syncing activity events.

**Request:**
```json
{
  "type": "message_received",
  "channelType": "telegram",
  "userId": "123456789",
  "userName": "@shailesh",
  "message": "Hello Pico!"
}
```

**Activity Types:**
- `session_start`
- `session_end`
- `message_received`
- `message_sent`
- `tool_executed`
- `error`
- `approval_request`
- `approval_resolved`
- `channel_connected`
- `channel_disconnected`

---

### Get Stats

```
GET /api/picobot/stats
```

Get Picobot statistics and status.

**Response:**
```json
{
  "success": true,
  "data": {
    "health": {
      "status": "active",
      "uptime": {}
    },
    "channels": [
      {
        "id": "ch_xxx",
        "name": "Telegram",
        "enabled": true,
        "status": "connected",
        "sessions": 5,
        "messagesToday": 42
      }
    ],
    "stats": {
      "totalSessions": 100,
      "activeSessions": 5,
      "todaySessions": 12,
      "messagesToday": 42
    },
    "recentActivity": [...]
  }
}
```

---

### Send Message

```
POST /api/picobot/send
```

Send a message through a channel.

**Request:**
```json
{
  "channelId": "telegram",
  "userId": "123456789",
  "message": "Hello from Soothsayer!"
}
```

---

### Toggle Channel

```
POST /api/picobot/channels/:id/toggle
```

Enable or disable a channel.

**Request:**
```json
{
  "enabled": true
}
```

---

### Get Pending Commands

```
GET /api/picobot/commands/pending
```

Get pending commands for Picobot to execute.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "cmd_xxx",
      "commandType": "send_message",
      "payload": {
        "channelId": "telegram",
        "message": "Scheduled reminder"
      },
      "status": "pending",
      "createdAt": "2024-01-15T10:00:00Z"
    }
  ]
}
```

---

### Acknowledge Command

```
POST /api/picobot/commands/:id/acknowledge
```

Mark command as acknowledged (being processed).

---

### Complete Command

```
POST /api/picobot/commands/:id/complete
```

Mark command as completed with result.

**Request:**
```json
{
  "result": {
    "success": true,
    "messageId": "msg_xxx"
  }
}
```

---

## Error Responses

```json
{
  "success": false,
  "error": {
    "code": "CHANNEL_NOT_FOUND",
    "message": "Channel with ID xxx not found"
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NOT_FOUND` | 404 | Resource not found |
| `UNAUTHORIZED` | 401 | Invalid or missing token |
| `CHANNEL_NOT_FOUND` | 404 | Channel doesn't exist |
| `PICOBOT_OFFLINE` | 503 | Picobot gateway is offline |
| `RATE_LIMITED` | 429 | Too many requests |

---

## Rate Limits

- `/stats`: 60 requests/minute
- `/send`: 30 requests/minute
- `/webhook/*`: 120 requests/minute
