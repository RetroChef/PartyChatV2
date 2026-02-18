export function TopBar({ isAuthenticated, username, links }) {
  return (
    <>
      <h2>
        Welcome, <span>{username}</span>!
      </h2>
      <div className="top-bar">
        <div className="auth-actions">
          {isAuthenticated ? (
            <a href={links.logout} className="btn danger">
              Logout
            </a>
          ) : (
            <>
              <a href={links.login} className="btn">
                Sign In
              </a>
              <a href={links.register} className="btn primary">
                Register
              </a>
            </>
          )}
        </div>
      </div>
    </>
  );
}
