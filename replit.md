# Chat Application

## Overview
A real-time chat application built with Flask and Flask-SocketIO. Supports public/private chat rooms, direct messages, user registration/login, profile onboarding, stickers, and WebSocket-based messaging.

## Recent Changes
- 2026-02-20: Migrated to Replit environment. Pinned Flask 3.0.x and Werkzeug 3.0.x for Flask-SocketIO compatibility. Updated SECRET_KEY to use SESSION_SECRET env var.

## Project Architecture
- **main.py**: Main application file with all routes, SocketIO events, and business logic (~1310 lines)
- **models.py**: SQLAlchemy models (User, Conversation, ConversationParticipant, Message)
- **templates/**: Jinja2 HTML templates (index, login, register, onboarding, create_room)
- **static/**: CSS, JS, icons, stickers, uploaded profile pictures
- **Database**: SQLite (chat.db)
- **Server**: Gunicorn with GeventWebSocket worker for WebSocket support

## Key Dependencies
- Flask 3.0.x, Werkzeug 3.0.x (pinned for Flask-SocketIO compatibility)
- Flask-SocketIO, Flask-Login, Flask-SQLAlchemy
- Gevent + gevent-websocket for WebSocket support

## Running
- Workflow: `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker main:app`
- Port: 5000

## User Preferences
- None documented yet
