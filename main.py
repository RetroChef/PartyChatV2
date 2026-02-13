# Imports here
import os
import random
import logging
from datetime import datetime
from typing import Dict, List, Optional
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# App Configuration Settings
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-key'),
    SQLALCHEMY_DATABASE_URI='sqlite:///chat.db',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    DEBUG=os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true'),
    CORS_ORIGINS='*',
    CHAT_ROOMS=[
        'General',
        'Education',
        'Technology',
        'Gaming'
    ]
)
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
socketio = SocketIO(
    app,
    cors_allowed_origins=app.config['CORS_ORIGINS'],
    logger=True,
    engineio_logger=True
)


# Login Loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# In-memory storage for active users
# In production, consider using Redis or another distributed storage
active_users: Dict[str, dict] = {}

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
    

    return render_template(
        'index.html',
        username=username,
        rooms=app.config['CHAT_ROOMS'],
        stickers=get_available_stickers()
    )

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
        
        emit('active_users', {
            'users': [user['username'] for user in active_users.values()]
        }, broadcast=True)
        
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
            }, broadcast=True)
            
            logger.info(f"User disconnected: {username}")
    
    except Exception as e:
        logger.error(f"Disconnection error: {str(e)}")

@socketio.on('join')
def on_join(data: dict):
    try:
        username = session['username']
        room = data['room']
        
        if room not in app.config['CHAT_ROOMS']:
            logger.warning(f"Invalid room join attempt: {room}")
            return
        
        join_room(room)
        active_users[request.sid]['room'] = room
        
        emit('status', {
            'msg': f'{username} has joined the room.',
            'type': 'join',
            'timestamp': datetime.now().isoformat()
        }, room=room)
        
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
        }, room=room)
        
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
        
        timestamp = datetime.now().isoformat()
        
        if msg_type == 'sticker':
            if room not in app.config['CHAT_ROOMS']:
                logger.warning(f"Sticker to invalid room: {room}")
                return

            file = data.get('file')
            if not file:
                logger.warning("Sticker missing file data")
                return

            emit('message', {
                'type': 'sticker',
                'username': username,
                'room': room,
                'file': file,
                'timestamp': timestamp
            }, room=room)

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
                        'from': username,
                        'to': target_user,
                        'file': file,
                        'timestamp': timestamp
                    }, room=sid)
                    logger.info(f"Private sticker sent: {username} -> {target_user}")
                    return

            logger.warning(f"Private sticker failed - user not found: {target_user}")
            return

        if not message:
            return

        if msg_type == 'private':
            # Handle private messages
            target_user = data.get('target')
            if not target_user:
                return
                
            for sid, user_data in active_users.items():
                if user_data['username'] == target_user:
                    emit('private_message', {
                        'msg': message,
                        'from': username,
                        'to': target_user,
                        'timestamp': timestamp
                    }, room=sid)
                    logger.info(f"Private message sent: {username} -> {target_user}")
                    return
                    
            logger.warning(f"Private message failed - user not found: {target_user}")
        
        else:
            # Regular room message
            if room not in app.config['CHAT_ROOMS']:
                logger.warning(f"Message to invalid room: {room}")
                return
                
            emit('message', {
                'msg': message,
                'username': username,
                'room': room,
                'timestamp': timestamp,
                'type': 'message'
            }, room=room)
            
            logger.info(f"Message sent in {room} by {username}")

    except Exception as e:
        logger.error(f"Message handling error: {str(e)}")

if __name__ == '__main__':
    # In production, use gunicorn or uwsgi instead
    port = int(os.environ.get('PORT', 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=app.config['DEBUG'],
        use_reloader=app.config['DEBUG']
    )