let socket = io();
let currentRoom = "General";
let username = document.getElementById("username").textContent;
let roomMessages = {};
let replyContext = null;
let roomPolicies = {};
let canSendInCurrentRoom = true;


const ROOM_MESSAGES_STORAGE_KEY = `partychat:roomMessages:${username}`;

hydrateRoomMessages();

const SWIPE_REPLY_THRESHOLD = 70;
const messageElementsById = new Map();

socket.on("connect", () => {
        joinRoom("General");
        highlightActiveRoom("General");
});

socket.on("message", (data) => {
        if (data.type === "sticker") {
                addStickerMessage(
                        data.username,
                        data.file,
                        data.username === username ? "own" : "other",
                );
                return;
        }

        addMessage({
                id: data.id,
                sender: data.username,
                message: data.msg,
                type: data.username === username ? "own" : "other",
                replyTo: data.reply_to || null,
        });
});

socket.on("private_message", (data) => {
        addMessage({
                id: data.id,
                sender: data.from,
                message: `[Private] ${data.msg}`,
                type: "private",
                replyTo: data.reply_to || null,
        });
});

socket.on("private_sticker", (data) => {
        addStickerMessage(data.from, data.file, "private");
});

socket.on("status", (data) => {
        addMessage({ sender: "System", message: data.msg, type: "system" });
});

socket.on("room_state", (data) => {
        if (!data || !data.room) {
                return;
        }

        roomPolicies[data.room] = data.message_policy || "everyone";
        if (data.room === currentRoom) {
                canSendInCurrentRoom = data.can_send_messages !== false;
                updateComposerAccess();
        }
});

socket.on("message_error", (data) => {
        const errorText = data && data.error
                ? data.error
                : "You are not allowed to send messages in this room.";
        showRoomFeedback(errorText, true);
});

socket.on("active_users", (data) => {
        const userList = document.getElementById("active-users");
        userList.innerHTML = data.users
                .map(
                        (user) => `
            <div class="user-item" onclick="insertPrivateMessage('${user}')">
                ${user} ${user === username ? "(you)" : ""}
            </div>
        `,
                )
                .join("");
});

function storeRoomMessage(messageData) {
        if (!roomMessages[currentRoom]) {
                roomMessages[currentRoom] = [];
        }

        roomMessages[currentRoom].push(messageData);
        persistRoomMessages();
}

function persistRoomMessages() {
        try {
                localStorage.setItem(
                        ROOM_MESSAGES_STORAGE_KEY,
                        JSON.stringify(roomMessages),
                );
        } catch (_error) {
                // Ignore storage write failures (private mode, quota exceeded, etc.)
        }
}

function hydrateRoomMessages() {
        try {
                const persisted = localStorage.getItem(
                        ROOM_MESSAGES_STORAGE_KEY,
                );
                if (!persisted) {
                        return;
                }

                const parsed = JSON.parse(persisted);
                if (parsed && typeof parsed === "object") {
                        roomMessages = parsed;
                }
        } catch (_error) {
                roomMessages = {};
        }
}

function sanitizeText(text) {
        return String(text || "")
                .replace(/\s+/g, " ")
                .trim();
}

function truncateText(text, maxLength = 90) {
        const clean = sanitizeText(text);
        if (clean.length <= maxLength) {
                return clean;
        }

        return `${clean.slice(0, maxLength - 1)}…`;
}

function buildReplyBlock(replyTo) {
        if (!replyTo || !replyTo.id) {
                return null;
        }

        const replyDiv = document.createElement("button");
        replyDiv.type = "button";
        replyDiv.className = "reply-reference";
        replyDiv.title = "Jump to replied message";
        replyDiv.innerHTML = `
                <span class="reply-reference-sender">${replyTo.sender || "Unknown"}</span>
                <span class="reply-reference-text">${truncateText(replyTo.msg || "(message unavailable)", 70)}</span>
        `;
        replyDiv.onclick = () => jumpToMessage(replyTo.id);
        return replyDiv;
}

function addMessage(messageData, shouldStore = true) {
        if (shouldStore) {
                storeRoomMessage(messageData);
        }

        const chat = document.getElementById("chat");
        const messageDiv = document.createElement("div");
        messageDiv.className = `message ${messageData.type}`;
        const msgId =
                messageData.id ||
                `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        messageDiv.dataset.messageId = msgId;

        if (messageData.type !== "system") {
                const replyBlock = buildReplyBlock(messageData.replyTo);
                if (replyBlock) {
                        messageDiv.appendChild(replyBlock);
                }
        }

        const textDiv = document.createElement("div");
        textDiv.className = "message-text";
        textDiv.textContent = `${messageData.sender}: ${messageData.message}`;
        messageDiv.appendChild(textDiv);

        if (messageData.type === "own" || messageData.type === "other") {
                bindSwipeReply(messageDiv, {
                        id: msgId,
                        sender: messageData.sender,
                        msg: messageData.message,
                });
        }

        chat.appendChild(messageDiv);
        messageElementsById.set(msgId, messageDiv);
        chat.scrollTop = chat.scrollHeight;
}

function addStickerMessage(sender, file, type = "other", shouldStore = true) {
        if (shouldStore) {
                storeRoomMessage({
                        sender,
                        message: file,
                        type: `sticker:${type}`,
                });
        }

        const chat = document.getElementById("chat");
        const messageDiv = document.createElement("div");
        const senderDiv = document.createElement("div");
        const image = document.createElement("img");

        messageDiv.className = `message sticker ${type}`;
        senderDiv.className = "sticker-sender";
        senderDiv.textContent = `${sender}:`;
        image.src = `/static/${file}`;
        image.alt = "Sticker";
        image.className = "sticker-image";

        messageDiv.appendChild(senderDiv);
        messageDiv.appendChild(image);
        chat.appendChild(messageDiv);
        chat.scrollTop = chat.scrollHeight;
}

function bindSwipeReply(element, context) {
        let startX = null;
        let isPointerDown = false;

        const onStart = (clientX) => {
                isPointerDown = true;
                startX = clientX;
                element.classList.add("swiping");
        };

        const onMove = (clientX) => {
                if (!isPointerDown || startX === null) {
                        return;
                }

                const deltaX = clientX - startX;
                if (deltaX > 0) {
                        element.style.transform = `translateX(${Math.min(deltaX, 80)}px)`;
                }
        };

        const onEnd = (clientX) => {
                if (!isPointerDown || startX === null) {
                        return;
                }

                const deltaX = clientX - startX;
                element.classList.remove("swiping");
                element.style.transform = "";
                isPointerDown = false;
                startX = null;

                if (deltaX >= SWIPE_REPLY_THRESHOLD) {
                        setReplyContext(context);
                }
        };

        element.addEventListener(
                "touchstart",
                (event) => onStart(event.touches[0].clientX),
                { passive: true },
        );
        element.addEventListener(
                "touchmove",
                (event) => onMove(event.touches[0].clientX),
                { passive: true },
        );
        element.addEventListener("touchend", (event) => {
                const touch = event.changedTouches[0];
                onEnd(touch ? touch.clientX : 0);
        });

        element.addEventListener("mousedown", (event) =>
                onStart(event.clientX),
        );
        element.addEventListener("mousemove", (event) => onMove(event.clientX));
        element.addEventListener("mouseup", (event) => onEnd(event.clientX));
        element.addEventListener("mouseleave", (event) => onEnd(event.clientX));
}

function setReplyContext(context) {
        replyContext = context;
        const preview = document.getElementById("reply-preview");
        const previewText = document.getElementById("reply-preview-text");
        previewText.textContent = `${context.sender}: ${truncateText(context.msg)}`;
        preview.classList.remove("hidden");
        document.getElementById("message").focus();
}

function clearReply() {
        replyContext = null;
        document.getElementById("reply-preview").classList.add("hidden");
        document.getElementById("reply-preview-text").textContent = "";
}

function jumpToMessage(messageId) {
        const target =
                messageElementsById.get(messageId) ||
                document.querySelector(`[data-message-id="${messageId}"]`);
        if (!target) {
                return;
        }

        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("message-highlight");
        setTimeout(() => target.classList.remove("message-highlight"), 1200);
}

function sendMessage() {
        const input = document.getElementById("message");
        const message = input.value.trim();

        if (!canSendInCurrentRoom) {
                showRoomFeedback(
                        "This room only allows messages from the host and moderators.",
                        true,
                );
                return;
        }

        if (!message) return;

        if (message.startsWith("@")) {
                const [target, ...msgParts] = message.substring(1).split(" ");
                const privateMsg = msgParts.join(" ");

                if (privateMsg) {
                        addMessage({
                                sender: username,
                                message: `[Private to ${target}] ${privateMsg}`,
                                type: "own",
                                replyTo: replyContext,
                        });

                        socket.emit("message", {
                                msg: privateMsg,
                                type: "private",
                                target: target,
                                reply_to: replyContext,
                        });
                }
        } else {
                socket.emit("message", {
                        msg: message,
                        room: currentRoom,
                        reply_to: replyContext,
                });
        }

        input.value = "";
        clearReply();
        input.focus();
}

function getPrivateTargetFromInput() {
        const input = document.getElementById("message");
        const message = input.value.trim();

        if (!message.startsWith("@")) {
                return null;
        }

        const [target] = message.substring(1).split(" ");
        return target || null;
}

function sendSticker(file) {
        const privateTarget = getPrivateTargetFromInput();
        
        if (!privateTarget && !canSendInCurrentRoom) {
                showRoomFeedback(
                        "This room only allows messages from the host and moderators.",
                        true,
                );
                return;
        }

        if (privateTarget) {
                addStickerMessage(username, file, "own");
                socket.emit("message", {
                        type: "private_sticker",
                        target: privateTarget,
                        file,
                });
                return;
        }

        socket.emit("message", {
                type: "sticker",
                room: currentRoom,
                file,
        });
}

function showRoomFeedback(message, isError = false) {
        const feedback = document.getElementById("room-access-feedback");
        if (!feedback) {
                return;
        }

        feedback.textContent = message;
        feedback.classList.toggle("error", isError);
}

function addRoomToList(room) {
        const roomItems = document.getElementById("room-items");
        if (!roomItems) {
                return;
        }

        const existing = Array.from(
                roomItems.querySelectorAll(".room-item"),
        ).find((item) => item.textContent.trim() === room);
        if (existing) {
                return;
        }

        const roomDiv = document.createElement("div");
        roomDiv.className = "room-item";
        roomDiv.textContent = room;
        roomDiv.onclick = () => joinRoom(room);
        roomItems.appendChild(roomDiv);
}

function toggleRoomAccess() {
        const panel = document.getElementById("room-access-panel");
        if (!panel) {
                return;
        }

        panel.classList.toggle("hidden");
        if (!panel.classList.contains("hidden")) {
                document.getElementById("room-code-input").focus();
        }
}

async function joinRoomByCode() {
        const roomCodeInput = document.getElementById("room-code-input");
        const roomCode = roomCodeInput.value.trim().toUpperCase();

        if (!roomCode) {
                showRoomFeedback("Please enter a room code.", true);
                return;
        }

        const formData = new URLSearchParams();
        formData.append("room_code", roomCode);

        try {
                const response = await fetch("/api/rooms/join", {
                        method: "POST",
                        headers: {
                                "Content-Type":
                                        "application/x-www-form-urlencoded",
                        },
                        body: formData,
                });

                const payload = await response.json();
                if (!response.ok) {
                        showRoomFeedback(
                                payload.error || "Unable to join room.",
                                true,
                        );
                        return;
                }
                roomPolicies[payload.room] = payload.message_policy || "everyone";
                addRoomToList(payload.room);
                joinRoom(payload.room);
                showRoomFeedback(`Joined room: ${payload.room}`);
                roomCodeInput.value = "";
        } catch (_error) {
                showRoomFeedback(
                        "Something went wrong. Please try again.",
                        true,
                );
        }
}

function toggleStickerBar() {
        const stickerBar = document.getElementById("sticker-bar");
        stickerBar.classList.toggle("collapsed");
}

function joinRoom(room) {
        socket.emit("leave", { room: currentRoom });
        currentRoom = room;
        canSendInCurrentRoom = true;
        updateComposerAccess();
        socket.emit("join", { room });

        highlightActiveRoom(room);
        clearReply();

        const chat = document.getElementById("chat");
        chat.innerHTML = "";
        messageElementsById.clear();

        if (roomMessages[room]) {
                roomMessages[room].forEach((msg) => {
                        if (
                                typeof msg.type === "string" &&
                                msg.type.startsWith("sticker:")
                        ) {
                                addStickerMessage(
                                        msg.sender,
                                        msg.message,
                                        msg.type.replace("sticker:", ""),
                                        false,
                                );
                        } else {
                                addMessage(msg, false);
                        }
                });
        }
}

function updateComposerAccess() {
        const messageInput = document.getElementById("message");
        const sendButton = document.getElementById("send-button");
        const stickerButton = document.getElementById("sticker-toggle");

        if (!messageInput || !sendButton || !stickerButton) {
                return;
        }

        messageInput.disabled = !canSendInCurrentRoom;
        sendButton.disabled = !canSendInCurrentRoom;
        stickerButton.disabled = !canSendInCurrentRoom;
        messageInput.placeholder = canSendInCurrentRoom
                ? "Type a message..."
                : "Only the room host and moderators can send messages";
}

function insertPrivateMessage(user) {
        document.getElementById("message").value = `@${user} `;
        document.getElementById("message").focus();
}

function handleKeyPress(event) {
        if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
        }
}
function handleRoomCodeEnter(event) {
        if (event.key === "Enter") {
                event.preventDefault();
                joinRoomByCode();
        }
}

document.addEventListener("DOMContentLoaded", () => {
        hydrateRoomMessages();
        if ("Notification" in window) {
                Notification.requestPermission();
        }
        const roomCodeInput = document.getElementById("room-code-input");
        if (roomCodeInput) {
                roomCodeInput.addEventListener("keypress", handleRoomCodeEnter);
        }

        const params = new URLSearchParams(window.location.search);
        const joinedRoom = params.get("joined");
        if (joinedRoom) {
                roomPolicies[joinedRoom] = roomPolicies[joinedRoom] || "everyone";
                addRoomToList(joinedRoom);
                joinRoom(joinedRoom);
                showRoomFeedback(`Joined room: ${joinedRoom}`);
        }
});

function highlightActiveRoom(room) {
        document.querySelectorAll(".room-item").forEach((item) => {
                item.classList.remove("active-room");
                if (item.textContent.trim() === room) {
                        item.classList.add("active-room");
                }
        });
}
