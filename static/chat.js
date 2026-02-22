let socket = io();
let currentRoom = "General";
let currentPrivateConversation = null;
let username = document.getElementById("username").textContent;
const isAuthenticated =
        document.getElementById("username").dataset.authenticated === "true";
let ownedRooms = new Set();
let roomMessages = {};
let replyContext = null;
let roomPolicies = {};
let canSendInCurrentRoom = true;
const privateConversationTargets = {};
const dmThreadsByConversationId = new Map();

const ROOM_MESSAGES_STORAGE_KEY = `partychat:roomMessages:${username}`;
const DEFAULT_AVATAR_PATH = "/static/icons/Guest.jpeg";
const currentUserAvatarSrc =
        document.querySelector(".profile-avatar")?.getAttribute("src") ||
        DEFAULT_AVATAR_PATH;

hydrateRoomMessages();

const SWIPE_REPLY_THRESHOLD = 70;
const messageElementsById = new Map();

socket.on("connect", () => {
        joinRoom("General");
        highlightActiveRoom("General");
});

socket.on("message", (data) => {
        const conversationKey = `room:${data.room || currentRoom}`;
        if (data.type === "sticker") {
                addStickerMessage(
                        data.username,
                        data.file,
                        data.username === username ? "own" : "other",
                        true,
                        conversationKey,
                        data.avatar_url || null,
                );
                return;
        }

        addMessage({
                id: data.id,
                sender: data.username,
                message: data.msg,
                type: data.username === username ? "own" : "other",
                threadType: "room",
                replyTo: data.reply_to || null,
                avatarUrl: data.avatar_url || null,
        }, true, conversationKey);
});

socket.on("room_history", (data) => {
        const roomName = data?.room;
        if (!roomName) {
                return;
        }

        const history = Array.isArray(data.messages) ? data.messages : [];
        const conversationKey = `room:${roomName}`;
        roomMessages[conversationKey] = history.map((msg) => {
                if (msg.type === "sticker") {
                        return {
                                id: msg.id,
                                sender: msg.username,
                                message: msg.file,
                                type: `sticker:${msg.username === username ? "own" : "other"}`,
                                threadType: "room",
                                avatarUrl: msg.avatar_url || null,
                                timestamp: msg.timestamp,
                        };
                }

                return {
                        id: msg.id,
                        sender: msg.username,
                        message: msg.msg || "",
                        type: msg.username === username ? "own" : "other",
                        threadType: "room",
                        replyTo: msg.reply_to || null,
                        avatarUrl: msg.avatar_url || null,
                        timestamp: msg.timestamp,
                };
        });
        persistRoomMessages();

        if (!currentPrivateConversation && currentRoom === roomName) {
                renderConversationMessages(conversationKey);
        }
});


function normalizeIncomingPrivateMessage(data) {
        const sender = data.from || "unknown";
        return {
                id: String(data.id),
                sender,
                message: data.msg || "",
                type: sender === username ? "own" : "private",
                threadType: "private",
                status: data.status || "sent",
                avatarUrl: data.avatar_url || null,
                delivered_at: data.delivered_at || null,
                read_at: data.read_at || null,
        };
}

socket.on("private_message", (data) => {
        const conversationId = String(data.conversation_id);
        const conversationKey = `private:${conversationId}`;
        privateConversationTargets[conversationId] = {
                username: data.from,
                display_name: data.from,
        };
        const previousUnreadCount =
                dmThreadsByConversationId.get(conversationId)?.unread_count || 0;
        upsertDmThread({
                conversation_id: conversationId,
                username: data.from,
                display_name: data.from,
                preview: data.msg || "",
                updated_at: data.timestamp || new Date().toISOString(),
                unread_count:
                        currentPrivateConversation &&
                        String(currentPrivateConversation.id) === conversationId
                                ? 0
                                : previousUnreadCount + 1,
        });

        addMessage(
                {
                        ...normalizeIncomingPrivateMessage(data),
                        replyTo: data.reply_to || null,
                },
                true,
                conversationKey,
        );
});

socket.on("private_sticker", (data) => {
        const conversationId = String(data.conversation_id);
        const conversationKey = `private:${conversationId}`;
        privateConversationTargets[conversationId] = {
                username: data.from,
                display_name: data.from,
        };
        const previousUnreadCount =
                dmThreadsByConversationId.get(conversationId)?.unread_count || 0;
        upsertDmThread({
                conversation_id: conversationId,
                username: data.from,
                display_name: data.from,
                preview: "📎 Sticker",
                updated_at: data.timestamp || new Date().toISOString(),
                unread_count:
                        currentPrivateConversation &&
                        String(currentPrivateConversation.id) === conversationId
                                ? 0
                                : previousUnreadCount + 1,
        });
        addStickerMessage(data.from, data.file, "private", true, conversationKey, data.avatar_url || null);
});


socket.on("private_message_batch", (data) => {
        const messages = Array.isArray(data?.messages) ? data.messages : [];
        messages.forEach((msg) => {
                const conversationId = String(msg.conversation_id);
                const conversationKey = `private:${conversationId}`;
                privateConversationTargets[conversationId] = {
                        username: msg.from,
                        display_name: msg.from,
                };
                const previousUnreadCount =
                        dmThreadsByConversationId.get(conversationId)?.unread_count || 0;
                upsertDmThread({
                        conversation_id: conversationId,
                        username: msg.from,
                        display_name: msg.from,
                        preview: msg.message_type === "private_sticker" ? "📎 Sticker" : msg.msg,
                        updated_at: msg.timestamp || new Date().toISOString(),
                        unread_count:
                                currentPrivateConversation &&
                                String(currentPrivateConversation.id) === conversationId
                                        ? 0
                                        : previousUnreadCount + 1,
                });

                if (msg.message_type === "private_sticker") {
                        addStickerMessage(msg.from, msg.file, "private", true, conversationKey, msg.avatar_url || null);
                        return;
                }

                addMessage(normalizeIncomingPrivateMessage(msg), true, conversationKey);
        });
});

socket.on("status", (data) => {
        addMessage({ sender: "System", message: data.msg, type: "system", threadType: "room" });
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

socket.on("room_expired", (data) => {
        if (!data || !data.room) {
                return;
        }

        showRoomFeedback(`Room expired: ${data.room}`, true);
        if (currentRoom === data.room) {
                currentRoom = "General";
                joinRoom("General");
        }
});

socket.on("active_users", (data) => {
        const users = Array.isArray(data.users) ? data.users : [];
        const normalizedUsers = users
                .map((entry) => {
                        if (typeof entry === "string") {
                                return {
                                        username: entry,
                                        avatar_url: DEFAULT_AVATAR_PATH,
                                };
                        }

                        return {
                                username: entry?.username || "",
                                avatar_url: entry?.avatar_url || DEFAULT_AVATAR_PATH,
                        };
                })
                .filter((userEntry) => Boolean(userEntry.username));

        const userList = document.getElementById("active-users");
        const title = document.getElementById("online-users-title");
        if (title) {
                title.textContent = `ONLINE — ${normalizedUsers.length}`;
        }

        userList.innerHTML = "";
        normalizedUsers.forEach((userEntry) => {
                const userItem = document.createElement("div");
                userItem.className = "user-item";
                userItem.addEventListener("click", () =>
                        insertPrivateMessage(userEntry.username),
                );

                const avatar = document.createElement("img");
                avatar.className = "user-item-avatar";
                avatar.src = userEntry.avatar_url;
                avatar.alt = `${userEntry.username} avatar`;

                const userLabel = document.createElement("span");
                userLabel.textContent = `${userEntry.username} ${
                        userEntry.username === username ? "(you)" : ""
                }`;

                userItem.append(avatar, userLabel);
                userList.appendChild(userItem);
        });
});


function getConversationStorageKey() {
        if (currentPrivateConversation) {
                return `private:${currentPrivateConversation.id}`;
        }
        return `room:${currentRoom}`;
}

function getPrivateConversationLabel() {
        if (!currentPrivateConversation) {
                return "";
        }

        return (
                currentPrivateConversation.display_name ||
                currentPrivateConversation.username ||
                "private chat"
        );
}

function setChatScope() {
        const chat = document.getElementById("chat");
        if (!chat) {
                return;
        }

        chat.classList.toggle("private-thread", Boolean(currentPrivateConversation));
}

function storeRoomMessage(messageData, conversationKey = getConversationStorageKey()) {
        if (!roomMessages[conversationKey]) {
                roomMessages[conversationKey] = [];
        }

        roomMessages[conversationKey].push(messageData);
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


function normalizeDmThread(thread) {
        if (!thread || thread.conversation_id == null) {
                return null;
        }

        const conversationId = String(thread.conversation_id);
        const usernameValue = thread.username || thread.partner_username || "";
        const displayName =
                thread.display_name ||
                thread.partner_display_name ||
                usernameValue ||
                "";
        const previewText = truncateText(thread.preview || "");
        return {
                conversation_id: conversationId,
                username: usernameValue,
                display_name: displayName,
                preview: previewText,
                updated_at: thread.updated_at || "",
                unread_count: Number(thread.unread_count || 0),
        };
}

function upsertDmThread(thread) {
        const normalized = normalizeDmThread(thread);
        if (!normalized) {
                return;
        }

        const existing = dmThreadsByConversationId.get(normalized.conversation_id) || {};
        const merged = {
                ...existing,
                ...normalized,
                username: normalized.username || existing.username || "",
                display_name: normalized.display_name || existing.display_name || "",
                preview: normalized.preview || existing.preview || "",
                updated_at: normalized.updated_at || existing.updated_at || "",
        };
        dmThreadsByConversationId.set(normalized.conversation_id, merged);

        if (merged.username || merged.display_name) {
                privateConversationTargets[normalized.conversation_id] = {
                        username: merged.username,
                        display_name: merged.display_name,
                };
        }

        renderDmChatList();
}

function renderDmChatList() {
        const container = document.getElementById("dm-chat-items");
        if (!container) {
                return;
        }

        const threads = Array.from(dmThreadsByConversationId.values()).sort((a, b) =>
                (b.updated_at || "").localeCompare(a.updated_at || ""),
        );

        container.innerHTML = "";
        threads.forEach((thread) => {
                const item = document.createElement("button");
                item.type = "button";
                item.className = "dm-chat-item";
                if (currentPrivateConversation && String(currentPrivateConversation.id) === String(thread.conversation_id)) {
                        item.classList.add("active-dm-chat");
                }
                if (Number(thread.unread_count || 0) > 0) {
                        item.classList.add("unread-dm");
                }

                const name = document.createElement("span");
                name.className = "dm-chat-item-name";
                name.textContent = thread.display_name || thread.username || "Direct chat";

                const preview = document.createElement("span");
                preview.className = "dm-chat-item-preview";
                preview.textContent = thread.preview || "No messages yet";

                item.appendChild(name);
                item.appendChild(preview);

                if (Number(thread.unread_count || 0) > 0) {
                        const badge = document.createElement("span");
                        badge.className = "dm-chat-item-badge";
                        badge.textContent = String(thread.unread_count);
                        item.appendChild(badge);
                }

                item.onclick = () =>
                        openPrivateConversation(thread.conversation_id, {
                                username: thread.username,
                                display_name: thread.display_name,
                        });
                container.appendChild(item);
        });
}

async function hydrateDmThreadList() {
        if (!isAuthenticated) {
                return;
        }

        try {
                const response = await fetch("/api/private-chats");
                const payload = await response.json();
                if (!response.ok) {
                        return;
                }

                const threads = Array.isArray(payload.threads) ? payload.threads : [];
                threads.forEach((thread) => upsertDmThread(thread));
        } catch (_error) {
                // best effort only
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

function resolveAvatarUrl(avatarUrl) {
        if (typeof avatarUrl === "string" && avatarUrl.trim()) {
                return avatarUrl;
        }
        return DEFAULT_AVATAR_PATH;
}

function formatTimestamp(timestampValue) {
        const date = timestampValue ? new Date(timestampValue) : new Date();
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function updateChatHeaderTitle() {
        const title = document.getElementById("chat-room-title");
        if (!title) {
                return;
        }

        if (currentPrivateConversation) {
                title.textContent = `@ ${getPrivateConversationLabel()}`;
                return;
        }

        title.textContent = `# ${currentRoom}`;
}

function shouldGroupWithPrevious(chat, sender) {
        const lastRow = chat.lastElementChild;
        if (!lastRow || !lastRow.classList || !lastRow.classList.contains("message-row")) {
                return false;
        }

        const previousSender = lastRow.dataset.sender || "";
        return previousSender === sender;
}

function buildMessageRow(messageData, messageNode) {
        if (messageData.type === "system") {
                return null;
        }

        const row = document.createElement("div");
        row.className = "message-row";
        if (messageData.type === "own") {
                row.classList.add("own");
        }

        const avatarLink = document.createElement("a");
        avatarLink.className = "message-avatar-link";
        avatarLink.href = `/profile/${encodeURIComponent(messageData.sender || "")}`;

        const avatarImage = document.createElement("img");
        avatarImage.className = "message-avatar";
        avatarImage.src = resolveAvatarUrl(messageData.avatarUrl);
        avatarImage.alt = `${messageData.sender || "User"} profile picture`;

        avatarLink.appendChild(avatarImage);
        row.appendChild(avatarLink);
        row.appendChild(messageNode);
        return row;
}

function addMessage(messageData, shouldStore = true, conversationKey = getConversationStorageKey()) {
        if (shouldStore) {
                storeRoomMessage(messageData, conversationKey);
        }

        if (conversationKey !== getConversationStorageKey()) {
                return;
        }

        const chat = document.getElementById("chat");
        const messageDiv = document.createElement("div");
        messageDiv.className = `message ${messageData.type}`;
        if (messageData.threadType === "private") {
                messageDiv.classList.add("thread-private");
        }
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

        const headerDiv = document.createElement("div");
        headerDiv.className = "message-header";

        const senderDiv = document.createElement("span");
        senderDiv.className = "message-sender";
        senderDiv.textContent = messageData.sender;

        const timestampDiv = document.createElement("span");
        timestampDiv.className = "message-timestamp";
        timestampDiv.textContent = formatTimestamp(messageData.timestamp || Date.now());

        headerDiv.appendChild(senderDiv);
        headerDiv.appendChild(timestampDiv);
        messageDiv.appendChild(headerDiv);

        const textDiv = document.createElement("div");
        textDiv.className = "message-text";
        const prefix = messageData.threadType === "private" ? "[Private] " : "";
        textDiv.textContent = `${prefix}${messageData.message}`;
        messageDiv.appendChild(textDiv);

        if (messageData.type === "own" || messageData.type === "other" || messageData.type === "private") {
                bindSwipeReply(messageDiv, {
                        id: msgId,
                        sender: messageData.sender,
                        msg: messageData.message,
                });
        }

        const row = buildMessageRow(messageData, messageDiv);
        if (row) {
                row.dataset.sender = messageData.sender || "";
                if (shouldGroupWithPrevious(chat, messageData.sender || "")) {
                        row.classList.add("grouped");
                }
        }
        chat.appendChild(row || messageDiv);
        messageElementsById.set(msgId, messageDiv);
        chat.scrollTop = chat.scrollHeight;
}

function addStickerMessage(sender, file, type = "other", shouldStore = true, conversationKey = getConversationStorageKey(), avatarUrl = null) {
        if (shouldStore) {
                const isPrivateThread =
                        conversationKey.startsWith("private:") || type === "private";
                storeRoomMessage(
                        {
                                sender,
                                message: file,
                                type: `sticker:${type}`,
                                threadType: isPrivateThread ? "private" : "room",
                                avatarUrl,
                        },
                        conversationKey,
                );
        }

        if (conversationKey !== getConversationStorageKey()) {
                return;
        }

        const chat = document.getElementById("chat");
        const messageDiv = document.createElement("div");
        const senderDiv = document.createElement("div");
        const image = document.createElement("img");

        messageDiv.className = `message sticker ${type}`;
        if (conversationKey.startsWith("private:")) {
                messageDiv.classList.add("thread-private");
        }
        senderDiv.className = "sticker-sender";
        senderDiv.textContent = `${sender}:`;
        image.src = `/static/${file}`;
        image.alt = "Sticker";
        image.className = "sticker-image";

        messageDiv.appendChild(senderDiv);
        messageDiv.appendChild(image);
        const row = buildMessageRow({ sender, type, avatarUrl }, messageDiv);
        if (row) {
                row.dataset.sender = sender || "";
                if (shouldGroupWithPrevious(chat, sender || "")) {
                        row.classList.add("grouped");
                }
        }
        chat.appendChild(row || messageDiv);
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

        if (currentPrivateConversation) {
                const target = currentPrivateConversation.username;
                addMessage({
                        sender: username,
                        message,
                        type: "own",
                        threadType: "private",
                        replyTo: replyContext,
                        avatarUrl: currentUserAvatarSrc,
                });
                upsertDmThread({
                        conversation_id: currentPrivateConversation.id,
                        username: currentPrivateConversation.username,
                        display_name: currentPrivateConversation.display_name,
                        preview: message,
                        updated_at: new Date().toISOString(),
                        unread_count: 0,
                });

                socket.emit("message", {
                        msg: message,
                        type: "private",
                        target,
                        reply_to: replyContext,
                });
        } else if (message.startsWith("@")) {
                const [target, ...msgParts] = message.substring(1).split(" ");
                const privateMsg = msgParts.join(" ");

                if (privateMsg) {
                        const conversationKey = `private:direct:${target}`;
                        addMessage(
                                {
                                        sender: username,
                                        message: privateMsg,
                                        type: "own",
                                        threadType: "private",
                                        replyTo: replyContext,
                                        avatarUrl: currentUserAvatarSrc,
                                },
                                true,
                                conversationKey,
                        );

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
        const privateTarget = currentPrivateConversation
                ? currentPrivateConversation.username
                : getPrivateTargetFromInput();

        if (!privateTarget && !canSendInCurrentRoom) {
                showRoomFeedback(
                        "This room only allows messages from the host and moderators.",
                        true,
                );
                return;
        }

        if (privateTarget) {
                addStickerMessage(username, file, "own", true, getConversationStorageKey(), currentUserAvatarSrc);
                if (currentPrivateConversation) {
                        upsertDmThread({
                                conversation_id: currentPrivateConversation.id,
                                username: currentPrivateConversation.username,
                                display_name: currentPrivateConversation.display_name,
                                preview: "📎 Sticker",
                                updated_at: new Date().toISOString(),
                                unread_count: 0,
                        });
                }
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

function addRoomToList(room, canDelete = false) {
        const roomItems = document.getElementById("room-items");
        if (!roomItems) {
                return;
        }

        const existing = Array.from(
                roomItems.querySelectorAll(".room-item[data-room-name]"),
        ).find((item) => item.dataset.roomName === room);
        if (existing) {
                if (canDelete && !existing.querySelector(".room-delete-btn")) {
                        const deleteButton = document.createElement("button");
                        deleteButton.type = "button";
                        deleteButton.className = "room-delete-btn";
                        deleteButton.title = "Delete room";
                        deleteButton.textContent = "🗑";
                        deleteButton.onclick = (event) =>
                                deleteRoomFromList(event, room);
                        existing.appendChild(deleteButton);
                }
                return;
        }

        const roomDiv = document.createElement("div");
        roomDiv.className = "room-item";
        roomDiv.dataset.roomName = room;
        roomDiv.onclick = () => joinRoom(room);

        const roomName = document.createElement("span");
        roomName.className = "room-name";
        roomName.textContent = room;
        roomDiv.appendChild(roomName);

        if (canDelete) {
                const deleteButton = document.createElement("button");
                deleteButton.type = "button";
                deleteButton.className = "room-delete-btn";
                deleteButton.title = "Delete room";
                deleteButton.textContent = "🗑";
                deleteButton.onclick = (event) => deleteRoomFromList(event, room);
                roomDiv.appendChild(deleteButton);
        }

        roomItems.appendChild(roomDiv);
}

async function deleteRoomFromList(event, room) {
        event.preventDefault();
        event.stopPropagation();

        const formData = new URLSearchParams();
        formData.append("room_name", room);

        try {
                const response = await fetch("/api/rooms/delete", {
                        method: "POST",
                        headers: {
                                "Content-Type": "application/x-www-form-urlencoded",
                        },
                        body: formData,
                });
                const payload = await response.json();

                if (!response.ok) {
                        showRoomFeedback(payload.error || "Unable to delete room.", true);
                        return;
                }

                ownedRooms.delete(room);
                const roomItem = document.querySelector(
                        `.room-item[data-room-name="${room}"]`,
                );
                if (roomItem) {
                        roomItem.remove();
                }

                if (currentRoom === room) {
                        joinRoom("General");
                }

                showRoomFeedback(`Deleted room: ${room}`);
        } catch (_error) {
                showRoomFeedback("Unable to delete room.", true);
        }
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

async function loadPrivateConversationHistory(conversationId) {
        const response = await fetch(
                `/api/private-chats/${conversationId}/messages?strategy=newest&limit=50`,
        );
        const payload = await response.json();
        if (!response.ok) {
                throw new Error(payload.error || "Unable to load conversation history.");
        }

        const key = `private:${conversationId}`;
        privateConversationTargets[conversationId] = payload.partner || {};
        const latestMessage = payload.messages[payload.messages.length - 1] || null;
        upsertDmThread({
                conversation_id: conversationId,
                username: payload.partner?.username || "",
                display_name: payload.partner?.display_name || payload.partner?.username || "",
                preview: latestMessage
                        ? latestMessage.message_type === "private_sticker"
                                ? "📎 Sticker"
                                : latestMessage.body || ""
                        : "",
                updated_at: latestMessage?.created_at || new Date().toISOString(),
                unread_count: 0,
        });
        roomMessages[key] = payload.messages.map((msg) => {
                if (msg.message_type === "private_sticker") {
                        return {
                                id: String(msg.id),
                                sender: msg.sender_username,
                                message: msg.sticker_file,
                                type: `sticker:${msg.sender_username === username ? "own" : "private"}`,
                                threadType: "private",
                                status: msg.status || "sent",
                                delivered_at: msg.delivered_at,
                                read_at: msg.read_at,
                        };
                }

                return {
                        id: String(msg.id),
                        sender: msg.sender_username,
                        message: msg.body || "",
                        type: msg.sender_username === username ? "own" : "private",
                        threadType: "private",
                        status: msg.status || "sent",
                        delivered_at: msg.delivered_at,
                        read_at: msg.read_at,
                };
        });
        persistRoomMessages();
}


async function markConversationRead(conversationId) {
        try {
                await fetch(`/api/private-chats/${conversationId}/read`, {
                        method: "POST",
                });
                socket.emit("mark_private_read", { conversation_id: conversationId });
                upsertDmThread({
                        conversation_id: conversationId,
                        unread_count: 0,
                });
        } catch (_error) {
                // best effort only
        }
}

async function openPrivateConversation(conversationId, target) {
        currentPrivateConversation = {
                id: conversationId,
                username: target.username,
                display_name: target.display_name,
        };
        setChatScope();
        updateComposerAccess();
        highlightActiveRoom(null);
        upsertDmThread({
                conversation_id: conversationId,
                username: target.username,
                display_name: target.display_name || target.username,
                unread_count: 0,
        });
        clearReply();

        try {
                await loadPrivateConversationHistory(conversationId);
                renderConversationMessages(getConversationStorageKey());
                updateChatHeaderTitle();
                await markConversationRead(conversationId);
                showRoomFeedback(`Private chat with ${getPrivateConversationLabel()}`);
        } catch (error) {
                showRoomFeedback(error.message, true);
        }
}

async function startPrivateChat() {
        if (!isAuthenticated) {
                showRoomFeedback("Sign in to start private chats.", true);
                return;
        }

        const search = window.prompt("Search users (optional):", "") || "";
        const query = search.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";

        try {
                const usersResponse = await fetch(`/api/users${query}`);
                const usersPayload = await usersResponse.json();
                if (!usersResponse.ok) {
                        showRoomFeedback(usersPayload.error || "Unable to load users.", true);
                        return;
                }

                if (!usersPayload.users || usersPayload.users.length === 0) {
                        showRoomFeedback("No users found for private chat.", true);
                        return;
                }

                const options = usersPayload.users
                        .map(
                                (user, index) =>
                                        `${index + 1}. ${user.display_name || user.username} (@${user.username})`,
                        )
                        .join("\n");
                const picked = window.prompt(
                        `Choose a user by number:
${options}`,
                        "1",
                );
                const idx = Number.parseInt(String(picked || ""), 10) - 1;
                if (!Number.isInteger(idx) || idx < 0 || idx >= usersPayload.users.length) {
                        showRoomFeedback("Private chat canceled.", true);
                        return;
                }

                const target = usersPayload.users[idx];
                const startResponse = await fetch("/api/private-chats/start", {
                        method: "POST",
                        headers: {
                                "Content-Type": "application/json",
                        },
                        body: JSON.stringify({ target_id: target.id }),
                });
                const startPayload = await startResponse.json();
                if (!startResponse.ok) {
                        showRoomFeedback(startPayload.error || "Unable to start private chat.", true);
                        return;
                }

                upsertDmThread({
                        conversation_id: startPayload.conversation_id,
                        username: startPayload.target.username,
                        display_name: startPayload.target.display_name || startPayload.target.username,
                        preview: "",
                        updated_at: new Date().toISOString(),
                        unread_count: 0,
                });
                await openPrivateConversation(startPayload.conversation_id, startPayload.target);
        } catch (_error) {
                showRoomFeedback("Unable to start private chat.", true);
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
                addRoomToList(payload.room, ownedRooms.has(payload.room));
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

function renderConversationMessages(conversationKey) {
        const chat = document.getElementById("chat");
        chat.innerHTML = "";
        messageElementsById.clear();

        if (!roomMessages[conversationKey]) {
                return;
        }

        roomMessages[conversationKey].forEach((msg) => {
                if (
                        typeof msg.type === "string" &&
                        msg.type.startsWith("sticker:")
                ) {
                        addStickerMessage(
                                msg.sender,
                                msg.message,
                                msg.type.replace("sticker:", ""),
                                false,
                                conversationKey,
                                msg.avatarUrl || null,
                        );
                } else {
                        addMessage(msg, false, conversationKey);
                }
        });
}

function joinRoom(room) {
        if (currentPrivateConversation) {
                currentPrivateConversation = null;
        }
        setChatScope();

        socket.emit("leave", { room: currentRoom });
        currentRoom = room;
        canSendInCurrentRoom = true;
        updateComposerAccess();
        socket.emit("join", { room });

        highlightActiveRoom(room);
        renderDmChatList();
        clearReply();
        renderConversationMessages(getConversationStorageKey());
        updateChatHeaderTitle();
}

function updateComposerAccess() {
        const messageInput = document.getElementById("message");
        const sendButton = document.getElementById("send-button");
        const stickerButton = document.getElementById("sticker-toggle");

        if (!messageInput || !sendButton || !stickerButton) {
                return;
        }

        const canSend = currentPrivateConversation ? true : canSendInCurrentRoom;
        messageInput.disabled = !canSend;
        sendButton.disabled = !canSend;
        stickerButton.disabled = !canSend;
        messageInput.placeholder = currentPrivateConversation
                ? `Message ${getPrivateConversationLabel()}...`
                : canSend
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

        document.querySelectorAll(".room-item[data-room-name]").forEach((item) => {
                if (item.querySelector(".room-delete-btn")) {
                        ownedRooms.add(item.dataset.roomName);
                }
        });
        if ("Notification" in window) {
                Notification.requestPermission();
        }
        const roomCodeInput = document.getElementById("room-code-input");
        if (roomCodeInput) {
                roomCodeInput.addEventListener("keypress", handleRoomCodeEnter);
        }

        hydrateDmThreadList();

        const toggleButton = document.getElementById("online-users-toggle");
        const onlineUsersPanel = document.getElementById("online-users-panel");
        const appShell = document.querySelector(".app-shell");
        if (toggleButton && onlineUsersPanel && appShell) {
                toggleButton.addEventListener("click", () => {
                        const isHidden = onlineUsersPanel.classList.toggle("hidden");
                        appShell.classList.toggle("online-users-hidden", isHidden);
                });
        }

        updateChatHeaderTitle();

        const params = new URLSearchParams(window.location.search);
        const joinedRoom = params.get("joined");
        const createdRoom = params.get("created");
        if (joinedRoom) {
                if (createdRoom === joinedRoom) {
                        ownedRooms.add(joinedRoom);
                }
                roomPolicies[joinedRoom] = roomPolicies[joinedRoom] || "everyone";
                addRoomToList(joinedRoom, ownedRooms.has(joinedRoom));
                joinRoom(joinedRoom);
                showRoomFeedback(`Joined room: ${joinedRoom}`);
        }
});

function highlightActiveRoom(room) {
        document.querySelectorAll(".room-item").forEach((item) => {
                item.classList.remove("active-room");
                if (item.dataset.roomName === room) {
                        item.classList.add("active-room");
                }
        });
        renderDmChatList();
}
