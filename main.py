# Imports here
import os
import random
import logging
import uuid
from datetime import datetime
from typing import Dict, List
import re

from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, current_user, logout_user, login_required
from werkzeug.middleware.proxy_fix import ProxyFix

from models import db, User

app = Flask(__name__)

# Config logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# App Configuration Settings
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-key'),
                  SQLALCHEMY_DATABASE_URI='sqlite:///chat.db',
                  SQLALCHEMY_TRACK_MODIFICATIONS=False,
                  DEBUG=os.environ.get('FLASK_DEBUG', 'false').lower()
                  in ('1', 'true'),
                  CORS_ORIGINS='*',
                  CHAT_ROOMS=[
                      'General', 'Study Corner', 'Games and Entertainment',
                      'Technology Nook'
                  ])
# Available chat rooms - stored as constant for now, could be moved to database

# Handle reverse proxy headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Extension

db.init_app(app)

with app.app_context():
    db.create_all()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize SocketIO with appropriate CORS settings
socketio = SocketIO(app,
                    cors_allowed_origins=app.config['CORS_ORIGINS'],
                    logger=True,
                    engineio_logger=True)


# Login Loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# In-memory storage for active users
# In production, consider using Redis or another distributed storage
active_users: Dict[str, dict] = {}
room_directory: Dict[str, dict] = {}
room_code_index: Dict[str, str] = {}

ALLOWED_STICKER_EXTENSIONS = {'.gif', '.png', '.jpg', '.jpeg', '.webp'}


def get_available_stickers() -> List[str]:
    """Return sticker file paths under static/stickers for rendering in the UI."""
    stickers_dir = os.path.join(app.static_folder, 'stickers')

    if not os.path.isdir(stickers_dir):
        logger.warning('Sticker directory does not exist: %s', stickers_dir)
        return []

    sticker_files: List[str] = []
    for filename in os.listdir(stickers_dir):
        _, ext = os.path.splitext(filename)
        if ext.lower() in ALLOWED_STICKER_EXTENSIONS:
            sticker_files.append(f'stickers/{filename}')

    return sorted(sticker_files)


def generate_room_code(length: int = 6) -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    while True:
        code = ''.join(random.choices(alphabet, k=length))
        if code not in room_code_index:
            return code


def add_room(room_name: str,
             is_public: bool = True,
             created_by: str = 'System') -> str:
    normalized_name = room_name.strip()
    if not normalized_name:
        raise ValueError('Room name cannot be empty')

    if normalized_name in room_directory:
        return room_directory[normalized_name]['code']

    code = generate_room_code()
    room_directory[normalized_name] = {
        'code': code,
        'is_public': is_public,
        'created_by': created_by,
        'created_at': datetime.now().isoformat()
    }
    room_code_index[code] = normalized_name
    return code


def get_public_rooms() -> List[str]:
    return [
        room_name for room_name, meta in room_directory.items()
        if meta.get('is_public')
    ]


def get_saved_private_rooms() -> List[str]:
    private_rooms = session.get('private_rooms', [])
    if not isinstance(private_rooms, list):
        return []

    return [
        room_name for room_name in private_rooms if room_name in room_directory
        and not room_directory[room_name].get('is_public', False)
    ]


def save_private_room(room_name: str) -> None:
    if room_name not in room_directory:
        return

    room_meta = room_directory.get(room_name, {})
    if room_meta.get('is_public', False):
        return

    private_rooms = get_saved_private_rooms()
    if room_name in private_rooms:
        return

    private_rooms.append(room_name)
    session['private_rooms'] = private_rooms


def get_rooms_for_sidebar() -> List[str]:
    rooms = list(get_public_rooms())
    for private_room in get_saved_private_rooms():
        if private_room not in rooms:
            rooms.append(private_room)
    return rooms


for default_room in app.config['CHAT_ROOMS']:
    add_room(default_room, is_public=True)


def generate_guest_username() -> str:
    """Generate a unique guest username with timestamp to avoid collisions"""
    timestamp = datetime.now().strftime('%H%M')
    return f'Guest{timestamp}{random.randint(1000,9999)}'


@app.route('/')
def index():
    if current_user.is_authenticated:
        username = current_user.username

    else:
        if 'username' not in session:
            session['username'] = generate_guest_username()
        username = session['username']

    return render_template('index.html',
                           username=username,
                           rooms=get_rooms_for_sidebar(),
                           stickers=get_available_stickers())


@app.route('/create-room')
def create_room_page():
    return render_template('create_room.html')


@app.route('/api/rooms', methods=['POST'])
def create_room():
    room_name = request.form.get('room_name', '').strip()
    visibility = request.form.get('visibility', 'public').strip().lower()
    is_public = visibility == 'public'

    if not room_name:
        return {'error': 'Room name is required'}, 400

    if len(room_name) > 60:
        return {'error': 'Room name must be 60 characters or fewer'}, 400

    if visibility not in ('public', 'private'):
        return {'error': 'Invalid visibility setting'}, 400

    created_by = current_user.username if current_user.is_authenticated else session.get(
        'username', 'Guest')
    room_code = add_room(room_name, is_public=is_public, created_by=created_by)

    if not is_public:
        save_private_room(room_name)

    return {'room': room_name, 'code': room_code, 'is_public': is_public}


@app.route('/api/rooms/join', methods=['POST'])
def join_room_by_code():
    room_code = request.form.get('room_code', '').strip().upper()
    if not room_code:
        return {'error': 'Room code is required'}, 400

    room_name = room_code_index.get(room_code)
    if not room_name:
        return {'error': 'Invalid room code'}, 404

    room_info = room_directory.get(room_name, {})
    return {
        'room': room_name,
        'code': room_code,
        'is_public': room_info.get('is_public', False)
    }


@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not (3 <= len(username) <= 80):
            flash('Username must be between 3 to 80 characters long', 'danger')
            return redirect(url_for('register'))

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash('Please enter a valid email address', 'danger')
            return redirect(url_for('register'))

        if password != confirm:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))

        user = User(username=username, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash('Account created successfully!', 'success')
        return redirect(url_for('index'))

    # GET request
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))

        flash('Invalid username or password', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    if current_user.is_authenticated:
        logout_user()

    session.pop('username', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


@socketio.event
def connect():
    try:
        if current_user.is_authenticated:
            username = current_user.username
            session['username'] = username

        else:
            if 'username' not in session:
                session['username'] = generate_guest_username()

        active_users[request.sid] = {
            'username': session['username'],
            'connected_at': datetime.now().isoformat()
        }

        emit('active_users',
             {'users': [user['username'] for user in active_users.values()]},
             broadcast=True)

        logger.info(f"User connected: {session['username']}")

    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        return False


@socketio.event
def disconnect():
    try:
        if request.sid in active_users:
            username = active_users[request.sid]['username']
            del active_users[request.sid]

            emit('active_users', {
                'users': [user['username'] for user in active_users.values()]
            },
                 broadcast=True)

            logger.info(f"User disconnected: {username}")

    except Exception as e:
        logger.error(f"Disconnection error: {str(e)}")


@socketio.on('join')
def on_join(data: dict):
    try:
        username = session['username']
        room = data['room']

        if room not in room_directory:
            logger.warning(f"Invalid room join attempt: {room}")
            return

        join_room(room)
        active_users[request.sid]['room'] = room

        emit('status', {
            'msg': f'{username} has joined the room.',
            'type': 'join',
            'timestamp': datetime.now().isoformat()
        },
             room=room)

        logger.info(f"User {username} joined room: {room}")

    except Exception as e:
        logger.error(f"Join room error: {str(e)}")


@socketio.on('leave')
def on_leave(data: dict):
    try:
        username = session['username']
        room = data['room']

        leave_room(room)
        if request.sid in active_users:
            active_users[request.sid].pop('room', None)

        emit('status', {
            'msg': f'{username} has left the room.',
            'type': 'leave',
            'timestamp': datetime.now().isoformat()
        },
             room=room)

        logger.info(f"User {username} left room: {room}")

    except Exception as e:
        logger.error(f"Leave room error: {str(e)}")


@socketio.on('message')
def handle_message(data: dict):
    try:
        username = session['username']
        room = data.get('room', 'General')
        msg_type = data.get('type', 'message')
        message = data.get('msg', '').strip()
        reply_to = data.get('reply_to')

        timestamp = datetime.now().isoformat()

        if msg_type == 'sticker':
            if room not in room_directory:
                logger.warning(f"Sticker to invalid room: {room}")
                return

            file = data.get('file')
            if not file:
                logger.warning("Sticker missing file data")
                return

            emit('message', {
                'id': str(uuid.uuid4()),
                'type': 'sticker',
                'username': username,
                'room': room,
                'file': file,
                'timestamp': timestamp
            },
                 room=room)

            logger.info(f"Sticker sent in {room} by {username}")
            return

        if msg_type == 'private_sticker':
            target_user = data.get('target')
            file = data.get('file')

            if not target_user or not file:
                logger.warning('Private sticker missing target or file')
                return

            for sid, user_data in active_users.items():
                if user_data['username'] == target_user:
                    emit('private_sticker', {
                        'id': str(uuid.uuid4()),
                        'from': username,
                        'to': target_user,
                        'file': file,
                        'timestamp': timestamp
                    },
                         room=sid)
                    logger.info(
                        f"Private sticker sent: {username} -> {target_user}")
                    return

            logger.warning(
                f"Private sticker failed - user not found: {target_user}")
            return

        if not message:
            return

        if msg_type == 'private':
            # Handle private messages
            target_user = data.get('target')
            if not target_user:
                return

            reply_payload = None
            if isinstance(reply_to, dict):
                reply_payload = {
                    'id': reply_to.get('id'),
                    'sender': reply_to.get('sender'),
                    'msg': reply_to.get('msg')
                }

            for sid, user_data in active_users.items():
                if user_data['username'] == target_user:
                    emit('private_message', {
                        'id': str(uuid.uuid4()),
                        'msg': message,
                        'from': username,
                        'to': target_user,
                        'timestamp': timestamp,
                        'reply_to': reply_payload
                    },
                         room=sid)
                    logger.info(
                        f"Private message sent: {username} -> {target_user}")
                    return

            logger.warning(
                f"Private message failed - user not found: {target_user}")

        else:
            # Regular room message
            if room not in room_directory:
                logger.warning(f"Message to invalid room: {room}")
                return

            reply_payload = None
            if isinstance(reply_to, dict):
                reply_payload = {
                    'id': reply_to.get('id'),
                    'sender': reply_to.get('sender'),
                    'msg': reply_to.get('msg')
                }

            emit('message', {
                'id': str(uuid.uuid4()),
                'msg': message,
                'username': username,
                'room': room,
                'timestamp': timestamp,
                'type': 'message',
                'reply_to': reply_payload
            },
                 room=room)

            logger.info(f"Message sent in {room} by {username}")

    except Exception as e:
        logger.error(f"Message handling error: {str(e)}")


if __name__ == '__main__':
    # In production, use gunicorn or uwsgi instead
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app,
                 host='0.0.0.0',
                 port=port,
                 debug=app.config['DEBUG'],
                 use_reloader=app.config['DEBUG'])
