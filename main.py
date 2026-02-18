# Imports here
import os
import random
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List
import re

from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, current_user, logout_user, login_required
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import inspect, text, func, distinct
from models import db, User, Conversation, ConversationParticipant, Message

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
def ensure_user_profile_columns() -> None:
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'user' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('user')}
    column_statements = {
        'display_name': 'ALTER TABLE user ADD COLUMN display_name VARCHAR(80)',
        'bio': 'ALTER TABLE user ADD COLUMN bio VARCHAR(500)',
        'avatar_url': 'ALTER TABLE user ADD COLUMN avatar_url VARCHAR(255)',
        'is_profile_complete': 'ALTER TABLE user ADD COLUMN is_profile_complete BOOLEAN NOT NULL DEFAULT 0'
    }

    missing_statements = [
        statement for column_name, statement in column_statements.items()
        if column_name not in existing_columns
    ]

    if not missing_statements:
        return

    with db.engine.begin() as conn:
        for statement in missing_statements:
            conn.execute(text(statement))

with app.app_context():
    db.create_all()
    ensure_user_profile_columns()
    
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
MESSAGE_POLICIES = {'everyone', 'host_mods_only'}
EXPIRATION_OPTIONS = {
    'never': None,
    '1_day': timedelta(days=1),
    '7_days': timedelta(days=7),
    '30_days': timedelta(days=30)
}
INACTIVITY_OPTIONS = {
    'none': None,
    '1_day': timedelta(days=1),
    '7_days': timedelta(days=7),
    '30_days': timedelta(days=30)
}

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
             created_by: str = 'System',
             message_policy: str = 'everyone',
             expires_in: str = 'never',
             archive_on_inactive: str = 'none') -> str:
    normalized_name = room_name.strip()
    if not normalized_name:
        raise ValueError('Room name cannot be empty')

    if normalized_name in room_directory:
        room_meta = room_directory[normalized_name]
        room_meta.setdefault('message_policy', 'everyone')
        room_meta.setdefault('moderators', [])
        room_meta.setdefault('expires_at', None)
        room_meta.setdefault('last_activity_at', room_meta.get('created_at'))
        room_meta.setdefault('archive_on_inactive', 'none')
        return room_meta['code']

    if message_policy not in MESSAGE_POLICIES:
        raise ValueError('Invalid message policy')
    if expires_in not in EXPIRATION_OPTIONS:
        raise ValueError('Invalid room expiration setting')
    if archive_on_inactive not in INACTIVITY_OPTIONS:
        raise ValueError('Invalid room inactivity setting')

    now = datetime.now()
    expires_delta = EXPIRATION_OPTIONS[expires_in]
    expires_at = (now + expires_delta).isoformat() if expires_delta else None
    code = generate_room_code()
    room_directory[normalized_name] = {
        'code': code,
        'is_public': is_public,
        'created_by': created_by,
        'created_at': now.isoformat(),
        'expires_at': expires_at,
        'last_activity_at': now.isoformat(),
        'archive_on_inactive': archive_on_inactive,
        'message_policy': message_policy,
        'moderators': []
    }
    room_code_index[code] = normalized_name
    return code

def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_room_expired(room_name: str) -> bool:
    room_meta = room_directory.get(room_name)
    if not room_meta:
        return True

    now = datetime.now()
    expires_at = parse_iso_datetime(room_meta.get('expires_at'))
    if expires_at and now >= expires_at:
        return True

    inactivity_key = room_meta.get('archive_on_inactive', 'none')
    inactivity_delta = INACTIVITY_OPTIONS.get(inactivity_key)
    if inactivity_delta:
        last_activity = parse_iso_datetime(room_meta.get('last_activity_at'))
        if not last_activity:
            last_activity = parse_iso_datetime(room_meta.get('created_at'))
        if last_activity and now >= last_activity + inactivity_delta:
            return True

    return False


def remove_room(room_name: str) -> None:
    room_meta = room_directory.pop(room_name, None)
    if not room_meta:
        return

    room_code = room_meta.get('code')
    if room_code:
        room_code_index.pop(room_code, None)


def cleanup_expired_rooms() -> None:
    expired_rooms = [room_name for room_name in room_directory if is_room_expired(room_name)]
    for room_name in expired_rooms:
        remove_room(room_name)

    private_rooms = session.get('private_rooms')
    if isinstance(private_rooms, list):
        session['private_rooms'] = [
            room_name for room_name in private_rooms if room_name in room_directory
        ]


def touch_room_activity(room_name: str) -> None:
    if room_name in room_directory:
        room_directory[room_name]['last_activity_at'] = datetime.now().isoformat()


def get_public_rooms() -> List[str]:
    cleanup_expired_rooms()
    return [
        room_name for room_name, meta in room_directory.items()
        if meta.get('is_public')
    ]

# tes
def get_saved_private_rooms() -> List[str]:
    cleanup_expired_rooms()
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
    cleanup_expired_rooms()
    rooms = list(get_public_rooms())
    for private_room in get_saved_private_rooms():
        if private_room not in rooms:
            rooms.append(private_room)
    return rooms




def get_owned_rooms(username: str) -> List[str]:
    return [
        room_name for room_name, meta in room_directory.items()
        if meta.get('created_by') == username
    ]
    
def get_user_by_username(username: str) -> User | None:
    if not username:
        return None
    return User.query.filter_by(username=username).first()


def get_or_create_direct_conversation(sender_id: int,
                                      recipient_id: int) -> Conversation:
    participant_ids = sorted([sender_id, recipient_id])

    candidate_conversation_ids = [
        conversation_id for conversation_id, in db.session.query(
            ConversationParticipant.conversation_id).filter(
                ConversationParticipant.user_id.in_(participant_ids)).group_by(
                    ConversationParticipant.conversation_id).having(
                        func.count(distinct(
                            ConversationParticipant.user_id)) == 2).all()
    ]

    for conversation_id in candidate_conversation_ids:
        participant_count = ConversationParticipant.query.filter_by(
            conversation_id=conversation_id).count()
        if participant_count == 2:
            conversation = Conversation.query.get(conversation_id)
            if conversation:
                return conversation

    conversation = Conversation(conversation_metadata={
        'type': 'direct',
        'participants': participant_ids
    })
    db.session.add(conversation)
    db.session.flush()

    db.session.add_all([
        ConversationParticipant(conversation_id=conversation.id,
                                user_id=sender_id),
        ConversationParticipant(conversation_id=conversation.id,
                                user_id=recipient_id)
    ])
    db.session.flush()
    return conversation


def find_active_user_sid(username: str) -> str | None:
    for sid, user_data in active_users.items():
        if user_data.get('username') == username:
            return sid
    return None

def get_room_message_policy(room_name: str) -> str:
    return room_directory.get(room_name, {}).get('message_policy', 'everyone')


def can_user_send_to_room(room_name: str, username: str) -> bool:
    room_meta = room_directory.get(room_name, {})
    if room_meta.get('message_policy', 'everyone') == 'everyone':
        return True

    if username == room_meta.get('created_by'):
        return True

    moderators = room_meta.get('moderators', [])
    if isinstance(moderators, list) and username in moderators:
        return True

    user_meta = active_users.get(request.sid, {})
    return bool(user_meta.get('is_moderator', False))


def emit_room_state(room_name: str, username: str) -> None:
    policy = get_room_message_policy(room_name)
    emit('room_state', {
        'room': room_name,
        'message_policy': policy,
        'can_send_messages': can_user_send_to_room(room_name, username)
    },
         room=request.sid)


for default_room in app.config['CHAT_ROOMS']:
    add_room(default_room, is_public=True)

CHAT_PROTECTED_ENDPOINTS = {
    'index',
    'create_room_page',
    'create_room',
    'delete_room',
    'join_room_by_code'
}


@app.before_request
def enforce_profile_completion():
    if not current_user.is_authenticated:
        return None

    if current_user.is_profile_complete:
        return None

    if request.endpoint is None:
        return None

    if request.endpoint == 'static':
        return None

    allowed_endpoints = {'onboarding', 'logout', 'login', 'register'}
    if request.endpoint in allowed_endpoints:
        return None

    if request.endpoint in CHAT_PROTECTED_ENDPOINTS:
        return redirect(url_for('onboarding'))

    return None

def generate_guest_username() -> str:
    """Generate a unique guest username with timestamp to avoid collisions"""
    timestamp = datetime.now().strftime('%H%M')
    return f'Guest{timestamp}{random.randint(1000,9999)}'


@app.route('/')
def index():
    cleanup_expired_rooms()
    if current_user.is_authenticated:
        username = current_user.username

    else:
        if 'username' not in session:
            session['username'] = generate_guest_username()
        username = session['username']

    return render_template('index.html',
                           username=username,
                           rooms=get_rooms_for_sidebar(),
                           owned_rooms=get_owned_rooms(username),
                           stickers=get_available_stickers())


@app.route('/create-room')
def create_room_page():
    cleanup_expired_rooms()
    return render_template('create_room.html')


@app.route('/api/rooms', methods=['POST'])
def create_room():
    cleanup_expired_rooms()
    room_name = request.form.get('room_name', '').strip()
    visibility = request.form.get('visibility', 'public').strip().lower()
    message_policy = request.form.get('message_policy', 'everyone').strip().lower()
    expires_in = request.form.get('expires_in', 'never').strip().lower()
    archive_on_inactive = request.form.get('archive_on_inactive', 'none').strip().lower()
    is_public = visibility == 'public'

    if not room_name:
        return {'error': 'Room name is required'}, 400

    if len(room_name) > 60:
        return {'error': 'Room name must be 60 characters or fewer'}, 400

    if visibility not in ('public', 'private'):
        return {'error': 'Invalid visibility setting'}, 400
    if message_policy not in MESSAGE_POLICIES:
        return {'error': 'Invalid message policy setting'}, 400
    if expires_in not in EXPIRATION_OPTIONS:
        return {'error': 'Invalid expires_in setting'}, 400
    if archive_on_inactive not in INACTIVITY_OPTIONS:
        return {'error': 'Invalid archive_on_inactive setting'}, 400

    created_by = current_user.username if current_user.is_authenticated else session.get(
        'username', 'Guest')
    room_code = add_room(room_name,
                         is_public=is_public,
                         created_by=created_by,
                         message_policy=message_policy,
                         expires_in=expires_in,
                         archive_on_inactive=archive_on_inactive)

    if not is_public:
        save_private_room(room_name)

    return {'room': room_name,
            'code': room_code,
            'is_public': is_public,
            'message_policy': message_policy,
            'expires_at': room_directory[room_name].get('expires_at')}


@app.route('/api/rooms/delete', methods=['POST'])
def delete_room():
    cleanup_expired_rooms()
    room_name = request.form.get('room_name', '').strip()
    if not room_name:
        return {'error': 'Room name is required'}, 400

    room_meta = room_directory.get(room_name)
    if not room_meta:
        return {'error': 'Room not found'}, 404

    requester = current_user.username if current_user.is_authenticated else session.get('username')
    if room_meta.get('created_by') != requester:
        return {'error': 'Only the room creator can delete this room'}, 403

    remove_room(room_name)
    return {'deleted': room_name}


@app.route('/api/rooms/join', methods=['POST'])
def join_room_by_code():
    cleanup_expired_rooms()
    room_code = request.form.get('room_code', '').strip().upper()
    if not room_code:
        return {'error': 'Room code is required'}, 400

    room_name = room_code_index.get(room_code)
    if not room_name:
        return {'error': 'Invalid room code'}, 404

    room_info = room_directory.get(room_name, {})
    if is_room_expired(room_name):
        remove_room(room_name)
        return {'error': 'This room has expired'}, 410

    if not room_info.get('is_public', False):
        save_private_room(room_name)

    return {
        'room': room_name,
        'code': room_code,
        'is_public': room_info.get('is_public', False),
        'message_policy': room_info.get('message_policy', 'everyone')
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
        flash('Account created successfully! Please complete your profile.', 'success')
        return redirect(url_for('onboarding'))

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


@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        bio = request.form.get('bio', '').strip()
        avatar_url = request.form.get('avatar_url', '').strip()

        if not (2 <= len(display_name) <= 80):
            flash('Display name must be between 2 and 80 characters.', 'danger')
            return redirect(url_for('onboarding'))

        if len(bio) > 500:
            flash('Bio must be 500 characters or fewer.', 'danger')
            return redirect(url_for('onboarding'))

        if avatar_url and not re.match(r'^https?://[^\s]+$', avatar_url):
            flash('Avatar URL must be a valid http(s) URL.', 'danger')
            return redirect(url_for('onboarding'))

        current_user.display_name = display_name
        current_user.bio = bio
        current_user.avatar_url = avatar_url
        current_user.is_profile_complete = True
        db.session.commit()

        flash('Profile completed successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('onboarding.html')

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
        cleanup_expired_rooms()
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
        cleanup_expired_rooms()

        if room not in room_directory:
            logger.warning(f"Invalid room join attempt: {room}")
            return
        if is_room_expired(room):
            remove_room(room)
            emit('room_expired', {'room': room}, room=request.sid)
            return

        join_room(room)
        active_users[request.sid]['room'] = room
        emit_room_state(room, username)

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
            if is_room_expired(room):
                remove_room(room)
                emit('room_expired', {'room': room}, room=request.sid)
                return
            
            if not can_user_send_to_room(room, username):
                emit('message_error', {
                    'error':
                    'This room only allows messages from the host and moderators.',
                    'room': room
                },
                     room=request.sid)
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
            touch_room_activity(room)

            logger.info(f"Sticker sent in {room} by {username}")
            return

        if msg_type == 'private_sticker':
            target_user = data.get('target')
            file = data.get('file')

            if not target_user or not file:
                logger.warning('Private sticker missing target or file')
                return

            sender_user = get_user_by_username(username)
            recipient_user = get_user_by_username(target_user)
            if not sender_user or not recipient_user:
                logger.warning(
                    'Private sticker failed - sender or recipient not found: %s -> %s',
                    username, target_user)
                emit('message_error', {
                    'error': 'Unable to resolve sender/recipient users.'
                },
                     room=request.sid)
                return

            conversation = get_or_create_direct_conversation(sender_user.id,
                                                             recipient_user.id)

            message_row = Message(conversation_id=conversation.id,
                                  sender_id=sender_user.id,
                                  recipient_id=recipient_user.id,
                                  message_type='private_sticker',
                                  sticker_file=file)
            db.session.add(message_row)
            db.session.commit()

            recipient_sid = find_active_user_sid(target_user)
            if recipient_sid:
                emit('private_sticker', {
                    'id': str(message_row.id),
                    'conversation_id': conversation.id,
                    'from': username,
                    'to': target_user,
                    'file': file,
                    'timestamp': message_row.created_at.isoformat()
                },
                     room=recipient_sid)
                message_row.delivered_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"Private sticker sent: {username} -> {target_user}")
            else:
                logger.info(
                    f"Private sticker queued (recipient offline): {username} -> {target_user}"
                )
            return

        if not message:
            return

        if msg_type == 'private':
            target_user = data.get('target')
            if not target_user:
                return
            sender_user = get_user_by_username(username)
            recipient_user = get_user_by_username(target_user)
            if not sender_user or not recipient_user:
                logger.warning(
                    'Private message failed - sender or recipient not found: %s -> %s',
                    username, target_user)
                emit('message_error', {
                    'error': 'Unable to resolve sender/recipient users.'
                },
                     room=request.sid)
                return

            conversation = get_or_create_direct_conversation(sender_user.id,
                                                             recipient_user.id)

            message_row = Message(conversation_id=conversation.id,
                                  sender_id=sender_user.id,
                                  recipient_id=recipient_user.id,
                                  body=message,
                                  message_type='private')
            db.session.add(message_row)
            db.session.commit()


            reply_payload = None
            if isinstance(reply_to, dict):
                reply_payload = {
                    'id': reply_to.get('id'),
                    'sender': reply_to.get('sender'),
                    'msg': reply_to.get('msg')
                }

            recipient_sid = find_active_user_sid(target_user)
            if recipient_sid:
                emit('private_message', {
                    'id': str(message_row.id),
                    'conversation_id': conversation.id,
                    'msg': message,
                    'from': username,
                    'to': target_user,
                    'timestamp': message_row.created_at.isoformat(),
                    'reply_to': reply_payload
                },
                     room=recipient_sid)
                message_row.delivered_at = datetime.utcnow()
                db.session.commit()
                logger.info(f"Private message sent: {username} -> {target_user}")
            else:
                logger.info(
                    f"Private message queued (recipient offline): {username} -> {target_user}"
                )

        else:
            # Regular room message
            if room not in room_directory:
                logger.warning(f"Message to invalid room: {room}")
                return
            if is_room_expired(room):
                remove_room(room)
                emit('room_expired', {'room': room}, room=request.sid)
                return
            
            if not can_user_send_to_room(room, username):
                emit('message_error', {
                    'error':
                    'This room only allows messages from the host and moderators.',
                    'room': room
                },
                     room=request.sid)
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
            touch_room_activity(room)

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
