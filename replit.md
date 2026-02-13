# Chat Application

## Overview
A real-time chat application built with Flask and Socket.IO. Supports multiple chat rooms, private messaging, sticker sharing, and user authentication (guest or registered).

## Project Architecture
- **Backend**: Python Flask with Flask-SocketIO for WebSocket support
- **Frontend**: HTML/CSS/JS served by Flask (Jinja2 templates)
- **Database**: SQLite (`chat.db`) via Flask-SQLAlchemy for user accounts
- **Auth**: Flask-Login for session management

## Key Files
- `main.py` - Main application entry point (Flask routes, SocketIO events)
- `models.py` - SQLAlchemy database models (User)
- `templates/` - Jinja2 HTML templates (index, login, register)
- `static/` - CSS, JS, stickers, and icons
- `static/chat.js` - Client-side chat logic and SocketIO integration

## Running
- Server runs on `0.0.0.0:5000` using gevent WebSocket
- Workflow: `python main.py`

## Recent Changes
- 2026-02-13: Initial setup in Replit environment
  - Created models.py with User model
  - Added database auto-creation on startup
  - Fixed Windows line endings
  - Fixed undefined ChatApp reference in chat.js
  - Installed Python dependencies
