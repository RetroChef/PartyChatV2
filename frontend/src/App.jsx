import { useCallback, useEffect, useMemo, useState } from 'react';
import { TopBar } from './components/TopBar';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { useChatSocket } from './hooks/useChatSocket';
import { hydrateRoomMessages, persistRoomMessages } from './utils/storage';

const bootstrap = window.PARTYCHAT_BOOTSTRAP;

function getConversationKey(currentRoom, currentPrivateConversation) {
  if (currentPrivateConversation) {
    return `private:${currentPrivateConversation.id}`;
  }
  return `room:${currentRoom}`;
}

export default function App() {
  const [rooms, setRooms] = useState(bootstrap.rooms || []);
  const [ownedRooms, setOwnedRooms] = useState(new Set(bootstrap.ownedRooms || []));
  const [currentRoom, setCurrentRoom] = useState('General');
  const [currentPrivateConversation, setCurrentPrivateConversation] = useState(null);
  const [roomMessages, setRoomMessages] = useState(() => hydrateRoomMessages(bootstrap.username));
  const [roomPolicies, setRoomPolicies] = useState({});
  const [canSendInCurrentRoom, setCanSendInCurrentRoom] = useState(true);
  const [activeUsers, setActiveUsers] = useState([]);
  const [message, setMessage] = useState('');
  const [stickerOpen, setStickerOpen] = useState(false);
  const [roomCode, setRoomCode] = useState('');
  const [feedback, setFeedback] = useState('');
  const [showRoomAccess, setShowRoomAccess] = useState(false);
  const [replyContext, setReplyContext] = useState(null);

  const conversationKey = useMemo(
    () => getConversationKey(currentRoom, currentPrivateConversation),
    [currentPrivateConversation, currentRoom]
  );

  useEffect(() => {
    persistRoomMessages(bootstrap.username, roomMessages);
  }, [roomMessages]);

  const appendMessage = useCallback(({ conversationKey: key, message: messageData }) => {
    setRoomMessages((prev) => ({
      ...prev,
      [key]: [...(prev[key] || []), messageData]
    }));
  }, []);

  const appendSticker = useCallback(({ sender, file, type, conversationKey: key }) => {
    const isPrivate = key.startsWith('private:') || type === 'private';
    appendMessage({
      conversationKey: key,
      message: {
        sender,
        message: file,
        type: `sticker:${type}`,
        threadType: isPrivate ? 'private' : 'room'
      }
    });
  }, [appendMessage]);

  const socket = useChatSocket({
    username: bootstrap.username,
    currentRoom,
    setActiveUsers,
    appendMessage,
    appendSticker,
    onRoomExpired: (data) => {
      if (data?.room && data.room === currentRoom) {
        setFeedback(`Room expired: ${data.room}`);
        setCurrentPrivateConversation(null);
        setCurrentRoom('General');
        socket.emit('join', { room: 'General' });
      }
    },
    onRoomState: (data) => {
      if (!data?.room) {
        return;
      }
      setRoomPolicies((prev) => ({ ...prev, [data.room]: data.message_policy || 'everyone' }));
      if (data.room === currentRoom) {
        setCanSendInCurrentRoom(data.can_send_messages !== false);
      }
    },
    onError: (data) => setFeedback(data?.error || 'Unable to send message.'),
    onConnect: () => {
      socket.emit('join', { room: 'General' });
    }
  });

  const joinRoom = (room) => {
    if (currentRoom) {
      socket.emit('leave', { room: currentRoom });
    }
    setCurrentPrivateConversation(null);
    setCurrentRoom(room);
    setCanSendInCurrentRoom(true);
    setReplyContext(null);
    socket.emit('join', { room });
  };

  const sendMessage = () => {
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }

    if (!currentPrivateConversation && !canSendInCurrentRoom) {
      setFeedback('This room only allows messages from the host and moderators.');
      return;
    }

    if (currentPrivateConversation) {
      appendMessage({
        conversationKey,
        message: {
          sender: bootstrap.username,
          message: trimmed,
          type: 'own',
          threadType: 'private',
          replyTo: replyContext
        }
      });
      socket.emit('message', {
        msg: trimmed,
        type: 'private',
        target: currentPrivateConversation.username,
        reply_to: replyContext
      });
    } else {
      appendMessage({
        conversationKey,
        message: { sender: bootstrap.username, message: trimmed, type: 'own', threadType: 'room', replyTo: replyContext }
      });
      socket.emit('message', { msg: trimmed, room: currentRoom, reply_to: replyContext });
    }

    setMessage('');
    setReplyContext(null);
  };

  const sendSticker = (sticker) => {
    if (currentPrivateConversation) {
      appendSticker({ sender: bootstrap.username, file: sticker, type: 'own', conversationKey });
      socket.emit('message', {
        msg: sticker,
        type: 'private_sticker',
        target: currentPrivateConversation.username,
        file: sticker
      });
      return;
    }

    appendSticker({ sender: bootstrap.username, file: sticker, type: 'own', conversationKey });
    socket.emit('message', { msg: sticker, room: currentRoom, type: 'sticker', file: sticker });
  };

  const deleteRoom = async (room) => {
    const formData = new URLSearchParams();
    formData.append('room_name', room);
    const response = await fetch('/api/rooms/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData
    });
    const payload = await response.json();
    if (!response.ok) {
      setFeedback(payload.error || 'Unable to delete room.');
      return;
    }

    setRooms((prev) => prev.filter((entry) => entry !== room));
    setOwnedRooms((prev) => new Set([...prev].filter((entry) => entry !== room)));
    if (currentRoom === room) {
      joinRoom('General');
    }
  };

  const joinByCode = async () => {
    const formData = new URLSearchParams();
    formData.append('room_code', roomCode.trim().toUpperCase());
    const response = await fetch('/api/rooms/join', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData
    });
    const payload = await response.json();
    if (!response.ok) {
      setFeedback(payload.error || 'Unable to join room.');
      return;
    }

    setRoomPolicies((prev) => ({ ...prev, [payload.room]: payload.message_policy || 'everyone' }));
    setRooms((prev) => (prev.includes(payload.room) ? prev : [...prev, payload.room]));
    joinRoom(payload.room);
    setRoomCode('');
    setFeedback(`Joined room: ${payload.room}`);
  };

  const startPrivateChat = async () => {
    if (!bootstrap.isAuthenticated) {
      setFeedback('Sign in to start private chats.');
      return;
    }

    const usersResp = await fetch('/api/users');
    const usersPayload = await usersResp.json();
    if (!usersResp.ok || !usersPayload.users?.length) {
      setFeedback(usersPayload.error || 'No users found for private chat.');
      return;
    }

    const picked = window.prompt(
      usersPayload.users.map((user, idx) => `${idx + 1}. ${user.display_name || user.username} (@${user.username})`).join('\n'),
      '1'
    );
    const index = Number.parseInt(String(picked || ''), 10) - 1;
    const target = usersPayload.users[index];
    if (!target) {
      setFeedback('Private chat canceled.');
      return;
    }

    const startResp = await fetch('/api/private-chats/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: target.id })
    });
    const startPayload = await startResp.json();
    if (!startResp.ok) {
      setFeedback(startPayload.error || 'Unable to start private chat.');
      return;
    }

    const conversationId = startPayload.conversation_id;
    const historyResp = await fetch(`/api/private-chats/${conversationId}/messages?strategy=newest&limit=50`);
    const historyPayload = await historyResp.json();
    if (!historyResp.ok) {
      setFeedback(historyPayload.error || 'Unable to load conversation history.');
      return;
    }

    setRoomMessages((prev) => ({
      ...prev,
      [`private:${conversationId}`]: historyPayload.messages.map((msg) => ({
        id: String(msg.id),
        sender: msg.sender_username,
        message: msg.message_type === 'private_sticker' ? msg.sticker_file : msg.body || '',
        type:
          msg.message_type === 'private_sticker'
            ? `sticker:${msg.sender_username === bootstrap.username ? 'own' : 'private'}`
            : msg.sender_username === bootstrap.username
              ? 'own'
              : 'private',
        threadType: 'private'
      }))
    }));

    setCurrentPrivateConversation({ id: conversationId, username: target.username, display_name: target.display_name });
    setFeedback(`Private chat with ${target.display_name || target.username}`);
  };

  const canSend = currentPrivateConversation ? true : canSendInCurrentRoom;
  const inputPlaceholder = currentPrivateConversation
    ? `Message ${currentPrivateConversation.display_name || currentPrivateConversation.username}...`
    : canSend
      ? 'Type a message...'
      : 'Only the room host and moderators can send messages';

  return (
    <>
      <TopBar isAuthenticated={bootstrap.isAuthenticated} username={bootstrap.username} links={bootstrap.links} />
      <div className="container">
        <Sidebar
          rooms={rooms}
          ownedRooms={ownedRooms}
          currentRoom={currentRoom}
          activeUsers={activeUsers}
          roomCode={roomCode}
          onRoomCodeChange={setRoomCode}
          onJoinRoom={joinRoom}
          onDeleteRoom={deleteRoom}
          onJoinByCode={joinByCode}
          onStartPrivateChat={startPrivateChat}
          showRoomAccess={showRoomAccess}
          setShowRoomAccess={setShowRoomAccess}
          createRoomUrl={bootstrap.links.createRoom}
          feedback={feedback}
        />
        <ChatView
          messages={roomMessages[conversationKey] || []}
          stickers={bootstrap.stickers}
          message={message}
          setMessage={setMessage}
          onSend={sendMessage}
          onSendSticker={sendSticker}
          stickerOpen={stickerOpen}
          setStickerOpen={setStickerOpen}
          replyContext={replyContext}
          clearReply={() => setReplyContext(null)}
          canSend={canSend}
          inputPlaceholder={inputPlaceholder}
          isPrivate={Boolean(currentPrivateConversation)}
        />
      </div>
    </>
  );
}
