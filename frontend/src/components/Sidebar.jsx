export function Sidebar({
  rooms,
  ownedRooms,
  currentRoom,
  activeUsers,
  roomCode,
  onRoomCodeChange,
  onJoinRoom,
  onDeleteRoom,
  onJoinByCode,
  onStartPrivateChat,
  showRoomAccess,
  setShowRoomAccess,
  createRoomUrl,
  feedback
}) {
  return (
    <div className="sidebar">
      <div className="room-list">
        <h3>Rooms</h3>
        <div id="room-items">
          {rooms.map((room) => (
            <div
              key={room}
              className={`room-item ${currentRoom === room ? 'active-room' : ''}`}
              data-room-name={room}
              onClick={() => onJoinRoom(room)}
            >
              <span className="room-name">{room}</span>
              {ownedRooms.has(room) && (
                <button
                  type="button"
                  className="room-delete-btn"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteRoom(room);
                  }}
                  title="Delete room"
                >
                  🗑
                </button>
              )}
            </div>
          ))}
        </div>

        <button
          id="toggle-room-access"
          className="room-access-btn"
          type="button"
          onClick={() => setShowRoomAccess((prev) => !prev)}
        >
          <span className="plus-icon">+</span> Create/Join a Room
        </button>
        <button id="new-private-chat" className="room-access-btn" type="button" onClick={onStartPrivateChat}>
          New private chat
        </button>

        <div id="room-access-panel" className={`room-access-panel ${showRoomAccess ? '' : 'hidden'}`}>
          <div className="room-join-row">
            <input
              id="room-code-input"
              type="text"
              placeholder="Enter room code"
              maxLength={10}
              value={roomCode}
              onChange={(e) => onRoomCodeChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  onJoinByCode();
                }
              }}
            />
            <button type="button" onClick={onJoinByCode}>
              Join
            </button>
          </div>
          <p className="room-create-hint">
            Don&apos;t want to join a room? <a href={createRoomUrl}>Create one</a> instead.
          </p>
          <p id="room-access-feedback" className="room-feedback">
            {feedback}
          </p>
        </div>
      </div>

      <div className="user-list">
        <h3>Online Users</h3>
        <div id="active-users">
          {activeUsers.map((user) => (
            <div className="user-item" key={user}>
              {user}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
