import { useEffect } from 'react';
import { io } from 'socket.io-client';

const socket = io();

export function useChatSocket({
  username,
  currentRoom,
  setActiveUsers,
  appendMessage,
  appendSticker,
  onRoomExpired,
  onRoomState,
  onError,
  onConnect
}) {
  useEffect(() => {
    const handleConnect = () => onConnect();
    const handleMessage = (data) => {
      if (data.type === 'sticker') {
        appendSticker({
          sender: data.username,
          file: data.file,
          type: data.username === username ? 'own' : 'other',
          conversationKey: `room:${data.room || currentRoom}`
        });
        return;
      }

      appendMessage({
        conversationKey: `room:${data.room || currentRoom}`,
        message: {
          id: data.id,
          sender: data.username,
          message: data.msg,
          type: data.username === username ? 'own' : 'other',
          threadType: 'room',
          replyTo: data.reply_to || null
        }
      });
    };

    const normalizePrivate = (data) => ({
      id: String(data.id),
      sender: data.from || 'unknown',
      message: data.msg || '',
      type: data.from === username ? 'own' : 'private',
      threadType: 'private',
      status: data.status || 'sent',
      delivered_at: data.delivered_at || null,
      read_at: data.read_at || null,
      replyTo: data.reply_to || null
    });

    const handlePrivateMessage = (data) => {
      appendMessage({
        conversationKey: `private:${data.conversation_id}`,
        message: normalizePrivate(data)
      });
    };

    const handlePrivateSticker = (data) => {
      appendSticker({
        sender: data.from,
        file: data.file,
        type: 'private',
        conversationKey: `private:${data.conversation_id}`
      });
    };

    const handlePrivateBatch = (data) => {
      const messages = Array.isArray(data?.messages) ? data.messages : [];
      messages.forEach((msg) => {
        const conversationKey = `private:${msg.conversation_id}`;
        if (msg.message_type === 'private_sticker') {
          appendSticker({
            sender: msg.from,
            file: msg.file,
            type: 'private',
            conversationKey
          });
          return;
        }

        appendMessage({
          conversationKey,
          message: normalizePrivate(msg)
        });
      });
    };

    const handleStatus = (data) => {
      appendMessage({
        conversationKey: `room:${currentRoom}`,
        message: { sender: 'System', message: data.msg, type: 'system', threadType: 'room' }
      });
    };

    const handleActiveUsers = (data) => setActiveUsers(data.users || []);

    socket.on('connect', handleConnect);
    socket.on('message', handleMessage);
    socket.on('private_message', handlePrivateMessage);
    socket.on('private_sticker', handlePrivateSticker);
    socket.on('private_message_batch', handlePrivateBatch);
    socket.on('status', handleStatus);
    socket.on('active_users', handleActiveUsers);
    socket.on('room_state', onRoomState);
    socket.on('room_expired', onRoomExpired);
    socket.on('message_error', onError);

    return () => {
      socket.off('connect', handleConnect);
      socket.off('message', handleMessage);
      socket.off('private_message', handlePrivateMessage);
      socket.off('private_sticker', handlePrivateSticker);
      socket.off('private_message_batch', handlePrivateBatch);
      socket.off('status', handleStatus);
      socket.off('active_users', handleActiveUsers);
      socket.off('room_state', onRoomState);
      socket.off('room_expired', onRoomExpired);
      socket.off('message_error', onError);
    };
  }, [appendMessage, appendSticker, currentRoom, onConnect, onError, onRoomExpired, onRoomState, setActiveUsers, username]);

  return socket;
}
