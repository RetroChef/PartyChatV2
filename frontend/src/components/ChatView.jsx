import { useMemo } from 'react';

export function ChatView({
  messages,
  stickers,
  message,
  setMessage,
  onSend,
  onSendSticker,
  stickerOpen,
  setStickerOpen,
  replyContext,
  clearReply,
  canSend,
  inputPlaceholder,
  isPrivate
}) {
  const threadClass = useMemo(() => (isPrivate ? 'private-thread' : ''), [isPrivate]);

  return (
    <div className="main-chat">
      <div id="chat" className={threadClass}>
        {messages.map((item, idx) => {
          if (String(item.type || '').startsWith('sticker:')) {
            return (
              <div className={`message sticker ${String(item.type).replace('sticker:', '')}`} key={`${item.id || idx}-sticker`}>
                <div className="sticker-sender">{item.sender}:</div>
                <img src={`/static/${item.message}`} alt="Sticker" className="sticker-image" />
              </div>
            );
          }

          return (
            <div className={`message ${item.type}`} key={item.id || `${idx}-${item.sender}`}>
              {item.replyTo && (
                <div className="reply-reference">
                  <span className="reply-reference-sender">{item.replyTo.sender || 'Unknown'}</span>
                  <span className="reply-reference-text">{item.replyTo.msg || '(message unavailable)'}</span>
                </div>
              )}
              <div className="message-text">
                {item.threadType === 'private' ? '[Private] ' : ''}
                {item.sender}: {item.message}
              </div>
            </div>
          );
        })}
      </div>

      <div id="sticker-bar" className={`sticker-bar ${stickerOpen ? '' : 'collapsed'}`}>
        {stickers.length > 0 ? (
          stickers.map((sticker) => (
            <button className="sticker-button" type="button" onClick={() => onSendSticker(sticker)} key={sticker}>
              <img src={`/static/${sticker}`} alt="Sticker" />
            </button>
          ))
        ) : (
          <p className="sticker-empty">No stickers found.</p>
        )}
      </div>

      <div id="reply-preview" className={`reply-preview ${replyContext ? '' : 'hidden'}`}>
        <div className="reply-preview-content">
          <div className="reply-preview-label">Replying to</div>
          <div id="reply-preview-text">{replyContext ? `${replyContext.sender}: ${replyContext.msg}` : ''}</div>
        </div>
        <button id="clear-reply" type="button" onClick={clearReply} aria-label="Clear reply">
          ✕
        </button>
      </div>

      <div className="input-area">
        <button id="sticker-toggle" type="button" onClick={() => setStickerOpen((prev) => !prev)} aria-label="Toggle stickers" disabled={!canSend}>
          <img src="/static/icons/sticker_btn.png" alt="Sticker button" />
        </button>
        <input
          id="message"
          type="text"
          placeholder={inputPlaceholder}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          disabled={!canSend}
        />
        <button id="send-button" onClick={onSend} disabled={!canSend}>
          Send
        </button>
      </div>
    </div>
  );
}
