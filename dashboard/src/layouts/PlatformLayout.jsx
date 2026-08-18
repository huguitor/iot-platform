import { Link, Outlet } from 'react-router-dom';

export default function PlatformLayout({ username, role, isAdminLike, onLogout }) {
  return (
    <div className="page">
      <header className="topbar">
        <Link className="brand platform-brand" to="/">
          <h1>IoT Platform</h1>
          <p className="muted">Sesión: {username} · {role}</p>
        </Link>

        <div className="platform-actions">
          {isAdminLike && (
            <>
              <Link className="ghost area-back-link" to="/plataforma/usuarios">Usuarios</Link>
              <Link className="ghost area-back-link" to="/plataforma/auditoria">Auditoría</Link>
            </>
          )}
          <button className="ghost" type="button" onClick={onLogout}>Cerrar sesión</button>
        </div>
      </header>

      <Outlet />
    </div>
  );
}
