import { useState } from 'react';
import {
  Navigate,
  Link,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { fetchCurrentUser, login } from './api';
import PlatformLayout from './layouts/PlatformLayout';
import MonitoringLayout from './layouts/MonitoringLayout';
import AdminPanel from './components/AdminPanel';
import AuditPanel from './components/AuditPanel';
import AppSelectorPage from './pages/AppSelectorPage';
import AccessControlPage from './pages/AccessControlPage';
import {
  canAccessPath,
  getHomeMode,
  resolvePostLoginPath,
} from './moduleRouting.mjs';

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [token, setToken] = useState('');
  const [currentUser, setCurrentUser] = useState(null);
  const [authError, setAuthError] = useState('');
  const [authForm, setAuthForm] = useState({
    username: 'admin',
    password: '',
  });

  const moduleAccess = {
    accessControl: Boolean(currentUser?.can_access_control),
    monitoring: Boolean(currentUser?.can_access_monitoring),
  };
  const hasBothModules = moduleAccess.accessControl && moduleAccess.monitoring;
  const isAdminLike = currentUser?.role === 'admin' || currentUser?.role === 'superadmin';

  const handleLogin = async (event) => {
    event.preventDefault();
    setAuthError('');
    const requestedPath = location.pathname;

    try {
      const { token: nextToken } = await login(authForm.username, authForm.password);
      const user = await fetchCurrentUser(nextToken);
      const access = {
        accessControl: Boolean(user.can_access_control),
        monitoring: Boolean(user.can_access_monitoring),
      };

      setToken(nextToken);
      setCurrentUser(user);
      navigate(resolvePostLoginPath(requestedPath, access), { replace: true });
    } catch (error) {
      console.error(error);
      setAuthError('Login failed');
      setToken('');
      setCurrentUser(null);
    }
  };

  const handleLogout = () => {
    setToken('');
    setCurrentUser(null);
    setAuthError('');
    navigate('/', { replace: true });
  };

  if (!token || !currentUser) {
    return (
      <LoginPage
        authForm={authForm}
        authError={authError}
        onChange={setAuthForm}
        onSubmit={handleLogin}
      />
    );
  }

  return (
    <Routes>
      <Route
        element={(
          <PlatformLayout
            username={currentUser.username}
            role={currentUser.role}
            isAdminLike={isAdminLike}
            onLogout={handleLogout}
          />
        )}
      >
        <Route index element={<HomeEntry moduleAccess={moduleAccess} />} />
        <Route
          path="acceso/*"
          element={
            canAccessPath('/acceso', moduleAccess)
              ? <AccessControlPage token={token} showPlatformHome={hasBothModules} />
              : <ModuleAccessDenied moduleName="Control de Acceso" moduleAccess={moduleAccess} />
          }
        />
        <Route
          path="monitoreo/*"
          element={
            canAccessPath('/monitoreo', moduleAccess)
              ? (
                <MonitoringLayout
                  token={token}
                  role={currentUser.role}
                  showPlatformHome={hasBothModules}
                />
              )
              : <ModuleAccessDenied moduleName="Monitoreo IoT" moduleAccess={moduleAccess} />
          }
        />
        <Route path="sensores/*" element={<LegacyMonitoringRedirect />} />
        <Route
          path="plataforma/usuarios"
          element={isAdminLike ? <AdminPanel token={token} role={currentUser.role} /> : <Navigate to="/" replace />}
        />
        <Route
          path="plataforma/auditoria"
          element={isAdminLike ? <AuditPanel token={token} /> : <Navigate to="/" replace />}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function HomeEntry({ moduleAccess }) {
  const homeMode = getHomeMode(moduleAccess);
  if (homeMode === 'access') {
    return <Navigate to="/acceso" replace />;
  }
  if (homeMode === 'monitoring') {
    return <Navigate to="/monitoreo" replace />;
  }
  if (homeMode === 'none') {
    return <NoModulesPage />;
  }
  return <AppSelectorPage />;
}

function LegacyMonitoringRedirect() {
  const location = useLocation();
  const suffix = location.pathname.slice('/sensores'.length);
  return <Navigate to={`/monitoreo${suffix}${location.search}${location.hash}`} replace />;
}

function ModuleAccessDenied({ moduleName, moduleAccess }) {
  const allowedPath = moduleAccess.accessControl
    ? '/acceso'
    : moduleAccess.monitoring
      ? '/monitoreo'
      : null;

  return (
    <main className="access-message">
      <p className="eyebrow">Acceso denegado</p>
      <h2>No tenés habilitado el módulo {moduleName}</h2>
      <p className="muted">Solicitá a un administrador que revise tus permisos de módulos.</p>
      {allowedPath && <Link className="primary area-back-link" to={allowedPath}>Ir al módulo habilitado</Link>}
    </main>
  );
}

function NoModulesPage() {
  return (
    <main className="access-message">
      <p className="eyebrow">Sin módulos habilitados</p>
      <h2>No tenés acceso a ningún área de IoT Platform</h2>
      <p className="muted">Solicitá a un administrador que habilite al menos un módulo.</p>
    </main>
  );
}

function LoginPage({ authForm, authError, onChange, onSubmit }) {
  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <h1>IoT Platform</h1>
          <p className="muted">Sin sesión</p>
        </div>
        <form className="auth-inline" onSubmit={onSubmit}>
          <input
            className="text-input"
            placeholder="Usuario"
            value={authForm.username}
            onChange={(event) => onChange({ ...authForm, username: event.target.value })}
          />
          <input
            type="password"
            className="text-input"
            placeholder="Contraseña"
            value={authForm.password}
            onChange={(event) => onChange({ ...authForm, password: event.target.value })}
          />
          <button className="primary" type="submit">Ingresar</button>
          {authError && <p className="error">{authError}</p>}
        </form>
      </header>
    </div>
  );
}
