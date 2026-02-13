let socket = io();
let currentRoom = 'General';
let username = document.getElementById('username').textContent;
let roomMessages = {};

// Socket event listeners
socket.on('connect', () => {
	joinRoom('General');
	highlightActiveRoom('General');
});

socket.on('message', (data) => {
	if (data.type === 'sticker') {
		addStickerMessage(data.username, data.file, data.username === username ? 'own' : 'other');
		return;
	}

	addMessage(
		data.username,
		data.msg,
		data.username === username ? 'own' : 'other'
	);
});

socket.on('private_message', (data) => {
	addMessage(data.from, `[Private] ${data.msg}`, 'private');
});

socket.on('private_sticker', (data) => {
	addStickerMessage(data.from, data.file, 'private');
});

socket.on('status', (data) => {
	addMessage('System', data.msg, 'system');
});

socket.on('active_users', (data) => {
	const userList = document.getElementById('active-users');
	userList.innerHTML = data.users
		.map(
			(user) => `
            <div class="user-item" onclick="insertPrivateMessage('${user}')">
                ${user} ${user === username ? '(you)' : ''}
            </div>
        `
		)
		.join('');
});

function storeRoomMessage(sender, message, type) {
	if (!roomMessages[currentRoom]) {
		roomMessages[currentRoom] = [];
	}

	roomMessages[currentRoom].push({ sender, message, type });
}

// Message handling
function addMessage(sender, message, type, shouldStore = true) {
	if (shouldStore) {
		storeRoomMessage(sender, message, type);
	}

	const chat = document.getElementById('chat');
	const messageDiv = document.createElement('div');
	messageDiv.className = `message ${type}`;
	messageDiv.textContent = `${sender}: ${message}`;

	chat.appendChild(messageDiv);
	chat.scrollTop = chat.scrollHeight;
}

function addStickerMessage(sender, file, type = 'other', shouldStore = true) {
	if (shouldStore) {
		storeRoomMessage(sender, file, `sticker:${type}`);
	}

	const chat = document.getElementById('chat');
	const messageDiv = document.createElement('div');
	const senderDiv = document.createElement('div');
	const image = document.createElement('img');

	messageDiv.className = `message sticker ${type}`;
	senderDiv.className = 'sticker-sender';
	senderDiv.textContent = `${sender}:`;
	image.src = `/static/${file}`;
	image.alt = 'Sticker';
	image.className = 'sticker-image';

	messageDiv.appendChild(senderDiv);
	messageDiv.appendChild(image);
	chat.appendChild(messageDiv);
	chat.scrollTop = chat.scrollHeight;
}

function sendMessage() {
	const input = document.getElementById('message');
	const message = input.value.trim();

	if (!message) return;

	if (message.startsWith('@')) {
		// Send private message
		const [target, ...msgParts] = message.substring(1).split(' ');
		const privateMsg = msgParts.join(' ');

		if (privateMsg) {
			addMessage(
				username,
				`[Private to ${target}] ${privateMsg}`,
				'own'
			);

			socket.emit('message', {
				msg: privateMsg,
				type: 'private',
				target: target,
			});
		}
	} else {
		// Send room message
		socket.emit('message', {
			msg: message,
			room: currentRoom,
		});
	}

	input.value = '';
	input.focus();
}

function getPrivateTargetFromInput() {
	const input = document.getElementById('message');
	const message = input.value.trim();

	if (!message.startsWith('@')) {
		return null;
	}

	const [target] = message.substring(1).split(' ');
	return target || null;
}

function sendSticker(file) {
	const privateTarget = getPrivateTargetFromInput();

	if (privateTarget) {
		addStickerMessage(username, file, 'own');
		socket.emit('message', {
			type: 'private_sticker',
			target: privateTarget,
			file,
		});
		return;
	}

	socket.emit('message', {
		type: 'sticker',
		room: currentRoom,
		file,
	});
}

function toggleStickerBar() {
	const stickerBar = document.getElementById('sticker-bar');
	stickerBar.classList.toggle('collapsed');
}

function joinRoom(room) {
	socket.emit('leave', { room: currentRoom });
	currentRoom = room;
	socket.emit('join', { room });

	highlightActiveRoom(room);

	// Show room history
	const chat = document.getElementById('chat');
	chat.innerHTML = '';

	if (roomMessages[room]) {
		roomMessages[room].forEach((msg) => {
			if (msg.type.startsWith('sticker:')) {
				addStickerMessage(msg.sender, msg.message, msg.type.replace('sticker:', ''), false);
			} else {
				addMessage(msg.sender, msg.message, msg.type, false);
			}
		});
	}
}

function insertPrivateMessage(user) {
	document.getElementById('message').value = `@${user} `;
	document.getElementById('message').focus();
}

function handleKeyPress(event) {
	if (event.key === 'Enter' && !event.shiftKey) {
		event.preventDefault();
		sendMessage();
	}
}

// Initialize chat when page loads
let chat;
document.addEventListener('DOMContentLoaded', () => {
	chat = new ChatApp();
	if ('Notification' in window) {
		Notification.requestPermission();
	}
});

// Add this new function to handle room highlighting
function highlightActiveRoom(room) {
	document.querySelectorAll('.room-item').forEach((item) => {
		item.classList.remove('active-room');
		if (item.textContent.trim() === room) {
			item.classList.add('active-room');
		}
	});
}
