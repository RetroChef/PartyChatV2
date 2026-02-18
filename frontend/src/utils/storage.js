export function getRoomMessageStorageKey(username) {
  return `partychat:roomMessages:${username}`;
}

export function hydrateRoomMessages(username) {
  const key = getRoomMessageStorageKey(username);
  try {
    const persisted = localStorage.getItem(key);
    if (!persisted) {
      return {};
    }

    const parsed = JSON.parse(persisted);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

export function persistRoomMessages(username, roomMessages) {
  const key = getRoomMessageStorageKey(username);
  try {
    localStorage.setItem(key, JSON.stringify(roomMessages));
  } catch (_error) {
    // ignore write errors
  }
}
