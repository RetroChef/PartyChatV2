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
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text, func, distinct, or_
from models import db, User, Conversation, ConversationParticipant, Message

app = Flask(__name__)

# Config logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# App Configuration Settings
app.config.update(SECRET_KEY=os.environ.get('SESSION_SECRET', 'dev-key'),
                  SQLALCHEMY_DATABASE_URI='sqlite:///chat.db',
                  SQLALCHEMY_TRACK_MODIFICATIONS=False,
                  DEBUG=os.environ.get('FLASK_DEBUG', 'false').lower()
                  in ('1', 'true'),
                  CORS_ORIGINS='*',
                  CHAT_ROOMS=[
                      'General', 'Study Corner', 'Games and Entertainment',
                      'Technology Nook'
                  ],
                  PROFILE_UPLOAD_FOLDER='uploads/profile_pictures',
                  PROFILE_UPLOAD_EXTENSIONS={'.jpg', '.jpeg', '.png', '.gif', '.webp'})
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

os.makedirs(os.path.join(app.static_folder, app.config['PROFILE_UPLOAD_FOLDER']),
            exist_ok=True)
    
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
user_presence: Dict[int, str] = {}
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
DEFAULT_AVATAR = 'icons/Guest.jpeg'


def get_default_avatar_path() -> str:
    return url_for('static', filename=DEFAULT_AVATAR)


def get_user_avatar_path(user: User | None) -> str:
    if user and user.avatar_url:
        return url_for('static', filename=user.avatar_url)
    return get_default_avatar_path()


def save_profile_image(uploaded_file) -> str | None:
    if not uploaded_file or not uploaded_file.filename:
        return None

    _, ext = os.path.splitext(uploaded_file.filename)
    ext = ext.lower()
    if ext not in app.config['PROFILE_UPLOAD_EXTENSIONS']:
        return None

    safe_name = secure_filename(uploaded_file.filename)
    unique_name = f'{uuid.uuid4().hex}_{safe_name}'
    relative_path = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], unique_name)
    absolute_path = os.path.join(app.static_folder, relative_path)
    uploaded_file.save(absolute_path)
    return relative_path.replace('\\', '/')


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


def build_active_users_payload() -> List[dict]:
    payload: List[dict] = []
    for user_data in active_users.values():
        username = user_data.get('username', '')
        if not username:
            continue

        avatar_url = user_data.get('avatar_url')
        if not avatar_url:
            user = get_user_by_username(username)
            avatar_url = get_user_avatar_path(user)

        payload.append({
            'username': username,
            'avatar_url': avatar_url
        })

    return payload


def get_user_by_id(user_id: int | str | None) -> User | None:
    if user_id in (None, ''):
        return None
    try:
        normalized = int(user_id)
    except (TypeError, ValueError):
        return None
    return User.query.get(normalized)


def get_private_conversation_partner(conversation_id: int,
                                     viewer_id: int) -> User | None:
    partner = db.session.query(User).join(
        ConversationParticipant,
        ConversationParticipant.user_id == User.id).filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id != viewer_id).first()
    return partner


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


def get_message_status(message: Message) -> str:
    if message.read_at:
        return 'read'
    if message.delivered_at:
        return 'delivered'
    return 'sent'


def serialize_private_message(message: Message, sender: User,
                              recipient: User) -> dict:
    return {
        'id': str(message.id),
        'conversation_id': message.conversation_id,
        'msg': message.body,
        'from': sender.username,
        'to': recipient.username,
        'message_type': message.message_type,
        'file': message.sticker_file,
        'timestamp': message.created_at.isoformat(),
        'delivered_at': message.delivered_at.isoformat()
        if message.delivered_at else None,
        'read_at': message.read_at.isoformat() if message.read_at else None,
        'status': get_message_status(message),
        'avatar_url': get_user_avatar_path(sender)
    }


def emit_missed_private_messages(user: User) -> None:
    pending_messages = Message.query.filter(
        Message.recipient_id == user.id, Message.delivered_at.is_(None)).order_by(
            Message.created_at.asc()).all()

    if not pending_messages:
        return

    sender_ids = {message.sender_id for message in pending_messages}
    sender_lookup = {
        sender.id: sender
        for sender in User.query.filter(User.id.in_(sender_ids)).all()
    }

    batch = []
    for message in pending_messages:
        sender = sender_lookup.get(message.sender_id)
        if not sender:
            continue
        batch.append(serialize_private_message(message, sender, user))

    if not batch:
        return

    emit('private_message_batch', {'messages': batch}, room=request.sid)
    delivered_at = datetime.utcnow()
    for message in pending_messages:
        message.delivered_at = delivered_at
    db.session.commit()


def mark_conversation_as_read(reader: User, conversation_id: int) -> dict | None:
    participant = ConversationParticipant.query.filter_by(
        conversation_id=conversation_id, user_id=reader.id).first()
    if not participant:
        return None

    unread_messages = Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.recipient_id == reader.id,
        Message.read_at.is_(None)).all()

    if not unread_messages:
        return {'updated': 0, 'read_at': None, 'message_ids': []}

    now = datetime.utcnow()
    sender_ids: set[int] = set()
    message_ids: list[int] = []
    for message in unread_messages:
        if not message.delivered_at:
            message.delivered_at = now
        message.read_at = now
        sender_ids.add(message.sender_id)
        message_ids.append(message.id)

    db.session.commit()

    receipt_payload = {
        'conversation_id': conversation_id,
        'reader_id': reader.id,
        'reader_username': reader.username,
        'message_ids': message_ids,
        'read_at': now.isoformat()
    }
    for sender_id in sender_ids:
        sender_sid = user_presence.get(sender_id)
        if sender_sid:
            emit('private_messages_read', receipt_payload, room=sender_sid)

    return {'updated': len(message_ids), 'read_at': now.isoformat(), 'message_ids': message_ids}

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
                           stickers=get_available_stickers(),
                           profile_avatar=get_user_avatar_path(current_user
                                                               if current_user.is_authenticated
                                                               else None),
                           profile_username=current_user.username if current_user.is_authenticated else username)


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


@app.route('/api/users', methods=['GET'])
@login_required
def list_chat_users():
    search_query = request.args.get('q', '').strip()

    users_query = User.query.filter(User.id != current_user.id)
    if search_query:
        pattern = f'%{search_query}%'
        users_query = users_query.filter(
            or_(User.username.ilike(pattern), User.display_name.ilike(pattern)))

    users = users_query.order_by(User.username.asc()).limit(50).all()
    return {
        'users': [{
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name,
            'avatar_url': get_user_avatar_path(user),
            'bio': user.bio or ''
        } for user in users]
    }


@app.route('/api/private-chats/start', methods=['POST'])
@login_required
def start_private_chat():
    payload = request.get_json(silent=True) or request.form
    target_id = payload.get('target_id')
    target_username = str(payload.get('target_username', '')).strip()

    target_user = None
    if target_id not in (None, ''):
        target_user = get_user_by_id(target_id)
    if target_user is None and target_username:
        target_user = get_user_by_username(target_username)

    if not target_user:
        return {'error': 'Target user not found.'}, 404
    if target_user.id == current_user.id:
        return {'error': 'You cannot start a private chat with yourself.'}, 400

    conversation = get_or_create_direct_conversation(current_user.id,
                                                     target_user.id)
    db.session.commit()

    return {
        'conversation_id': conversation.id,
        'target': {
            'id': target_user.id,
            'username': target_user.username,
            'display_name': target_user.display_name,
            'avatar_url': get_user_avatar_path(target_user),
            'bio': target_user.bio or ''
        }
    }


@app.route('/api/private-chats', methods=['GET'])
@login_required
def list_private_chats():
    participant_rows = db.session.query(ConversationParticipant.conversation_id).filter(
        ConversationParticipant.user_id == current_user.id).all()
    conversation_ids = [row.conversation_id for row in participant_rows]
    if not conversation_ids:
        return {'threads': []}

    latest_message_sq = db.session.query(
        Message.conversation_id.label('conversation_id'),
        func.max(Message.id).label('last_message_id')).filter(
            Message.conversation_id.in_(conversation_ids)).group_by(
                Message.conversation_id).subquery()

    unread_rows = db.session.query(
        Message.conversation_id,
        func.count(Message.id).label('unread_count')).filter(
            Message.recipient_id == current_user.id,
            Message.read_at.is_(None),
            Message.conversation_id.in_(conversation_ids)).group_by(
                Message.conversation_id).all()
    unread_by_conversation = {
        row.conversation_id: int(row.unread_count)
        for row in unread_rows
    }

    rows = db.session.query(Conversation.id, Message).outerjoin(
        latest_message_sq, latest_message_sq.c.conversation_id == Conversation.id).outerjoin(
            Message, Message.id == latest_message_sq.c.last_message_id).filter(
                Conversation.id.in_(conversation_ids)).all()

    threads = []
    for conversation_id, latest_message in rows:
        partner = get_private_conversation_partner(conversation_id, current_user.id)
        preview = ''
        updated_at = datetime.utcnow().isoformat()
        if latest_message:
            updated_at = latest_message.created_at.isoformat()
            if latest_message.message_type == 'private_sticker':
                preview = '📎 Sticker'
            else:
                preview = latest_message.body or ''

        threads.append({
            'conversation_id': conversation_id,
            'username': partner.username if partner else '',
            'display_name': partner.display_name if partner else '',
            'preview': preview,
            'updated_at': updated_at,
            'unread_count': unread_by_conversation.get(conversation_id, 0)
        })

    threads.sort(key=lambda thread: thread.get('updated_at', ''), reverse=True)
    return {'threads': threads}



@app.route('/api/private-chats/<int:conversation_id>/messages', methods=['GET'])
@login_required
def private_chat_messages(conversation_id: int):
    member = ConversationParticipant.query.filter_by(
        conversation_id=conversation_id, user_id=current_user.id).first()
    if not member:
        return {'error': 'Conversation not found.'}, 404

    strategy = request.args.get('strategy', 'newest').strip().lower()
    if strategy not in ('oldest', 'newest'):
        return {'error': 'Invalid strategy. Use oldest or newest.'}, 400

    try:
        limit = int(request.args.get('limit', 30))
    except ValueError:
        return {'error': 'limit must be an integer.'}, 400
    limit = max(1, min(limit, 100))

    before_id = request.args.get('before_id', type=int)
    after_id = request.args.get('after_id', type=int)

    query = db.session.query(Message, User.username, User.display_name).join(
        User, User.id == Message.sender_id).filter(
            Message.conversation_id == conversation_id)

    if strategy == 'newest':
        if before_id:
            query = query.filter(Message.id < before_id)
        query = query.order_by(Message.id.desc())
    else:
        if after_id:
            query = query.filter(Message.id > after_id)
        query = query.order_by(Message.id.asc())

    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    if strategy == 'newest':
        rows.reverse()

    partner = get_private_conversation_partner(conversation_id, current_user.id)
    messages = []
    for message, sender_username, sender_display_name in rows:
        messages.append({
            'id': message.id,
            'conversation_id': conversation_id,
            'sender_id': message.sender_id,
            'sender_username': sender_username,
            'sender_display_name': sender_display_name,
            'body': message.body,
            'message_type': message.message_type,
            'sticker_file': message.sticker_file,
            'created_at': message.created_at.isoformat(),
            'delivered_at': message.delivered_at.isoformat() if message.delivered_at else None,
            'read_at': message.read_at.isoformat() if message.read_at else None,
            'status': get_message_status(message)
        })

    next_cursor = None
    if has_more and messages:
        if strategy == 'newest':
            next_cursor = {'before_id': messages[0]['id']}
        else:
            next_cursor = {'after_id': messages[-1]['id']}

    return {
        'conversation_id': conversation_id,
        'strategy': strategy,
        'messages': messages,
        'has_more': has_more,
        'next_cursor': next_cursor,
        'partner': {
            'id': partner.id,
            'username': partner.username,
            'display_name': partner.display_name
        } if partner else None
    }


@app.route('/api/private-chats/<int:conversation_id>/read', methods=['POST'])
@login_required
def mark_private_chat_read(conversation_id: int):
    result = mark_conversation_as_read(current_user, conversation_id)
    if result is None:
        return {'error': 'Conversation not found.'}, 404

    return {
        'conversation_id': conversation_id,
        'updated': result['updated'],
        'read_at': result['read_at'],
        'message_ids': result['message_ids']
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
        profile_image = request.files.get('profile_image')

        if not (2 <= len(display_name) <= 80):
            flash('Display name must be between 2 and 80 characters.', 'danger')
            return redirect(url_for('onboarding'))

        if len(bio) > 500:
            flash('Bio must be 500 characters or fewer.', 'danger')
            return redirect(url_for('onboarding'))

        if profile_image and profile_image.filename:
            _, ext = os.path.splitext(profile_image.filename)
            if ext.lower() not in app.config['PROFILE_UPLOAD_EXTENSIONS']:
                flash('Profile image must be jpg, jpeg, png, gif, or webp.', 'danger')
                return redirect(url_for('onboarding'))

        saved_avatar_path = save_profile_image(profile_image) if profile_image else None

        current_user.display_name = display_name
        current_user.bio = bio
        if saved_avatar_path:
            current_user.avatar_url = saved_avatar_path
        current_user.is_profile_complete = True
        db.session.commit()

        flash('Profile completed successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('onboarding.html',
                           profile_avatar=get_user_avatar_path(current_user))


@app.route('/profile/<string:username>')
def profile(username: str):
    user = User.query.filter_by(username=username).first()
    if not user:
        flash('User profile not found.', 'danger')
        return redirect(url_for('index'))

    return render_template('profile.html',
                           profile_user=user,
                           profile_avatar=get_user_avatar_path(user),
                           default_avatar=get_default_avatar_path())

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
            'avatar_url': get_user_avatar_path(current_user if current_user.is_authenticated else None),
            'connected_at': datetime.now().isoformat()
        }

        if current_user.is_authenticated:
            user_presence[current_user.id] = request.sid
            emit_missed_private_messages(current_user)

        emit('active_users',
             {'users': build_active_users_payload()},
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

            stale_user_ids = [
                user_id for user_id, sid in user_presence.items() if sid == request.sid
            ]
            for user_id in stale_user_ids:
                user_presence.pop(user_id, None)

            emit('active_users', {
                'users': build_active_users_payload()
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
        sender_user = get_user_by_username(username)
        sender_avatar_url = get_user_avatar_path(sender_user)
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
                'timestamp': timestamp,
                'avatar_url': sender_avatar_url
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
                delivered_at = datetime.utcnow()
                message_row.delivered_at = delivered_at
                db.session.commit()
                emit('private_sticker', {
                    'id': str(message_row.id),
                    'conversation_id': conversation.id,
                    'from': username,
                    'to': target_user,
                    'file': file,
                    'timestamp': message_row.created_at.isoformat(),
                    'delivered_at': delivered_at.isoformat(),
                    'read_at': None,
                    'status': 'delivered',
                    'avatar_url': sender_avatar_url
                },
                     room=recipient_sid)
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
                delivered_at = datetime.utcnow()
                message_row.delivered_at = delivered_at
                db.session.commit()
                emit('private_message', {
                    'id': str(message_row.id),
                    'conversation_id': conversation.id,
                    'msg': message,
                    'from': username,
                    'to': target_user,
                    'timestamp': message_row.created_at.isoformat(),
                    'delivered_at': delivered_at.isoformat(),
                    'read_at': None,
                    'status': 'delivered',
                    'reply_to': reply_payload,
                    'avatar_url': sender_avatar_url
                },
                     room=recipient_sid)
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
                'reply_to': reply_payload,
                'avatar_url': sender_avatar_url
            },
                 room=room)
            touch_room_activity(room)

            logger.info(f"Message sent in {room} by {username}")

    except Exception as e:
        logger.error(f"Message handling error: {str(e)}")


@socketio.on('mark_private_read')
def on_mark_private_read(data: dict):
    if not current_user.is_authenticated:
        emit('message_error', {'error': 'Authentication required.'}, room=request.sid)
        return

    conversation_id = data.get('conversation_id')
    try:
        normalized_conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        emit('message_error', {'error': 'Invalid conversation id.'}, room=request.sid)
        return

    result = mark_conversation_as_read(current_user, normalized_conversation_id)
    if result is None:
        emit('message_error', {'error': 'Conversation not found.'}, room=request.sid)
        return

    emit('mark_private_read_ack', {
        'conversation_id': normalized_conversation_id,
        'updated': result['updated'],
        'read_at': result['read_at'],
        'message_ids': result['message_ids']
    }, room=request.sid)


if __name__ == '__main__':
    # In production, use gunicorn or uwsgi instead
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app,
                 host='0.0.0.0',
                 port=port,
                 debug=app.config['DEBUG'],
                 use_reloader=app.config['DEBUG'])
