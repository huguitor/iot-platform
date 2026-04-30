// dashboard/src/App.jsx

import { useEffect, useMemo, useState } from 'react';
import { fetchDevices, fetchTelemetry, login, sendCommand } from './api';

import DeviceList from './components/DeviceList';
import DeviceDetail from './components/DeviceDetail';
import AdminPanel from './components/AdminPanel';
import AuditPanel from './components/AuditPanel';
import MqttEventsPanel from './components/MqttEventsPanel';

// Nueva pantalla exclusiva para Control de Acceso
import AccessControlPage from './pages/AccessControlPage';

export default function App() {
  const [devices, setDevices] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [telemetry, setTelemetry] = useState([]);

  const [token, setToken] = useState('');
  const [role, setRole] = useState('user');

  const [authError, setAuthError] = useState('');

  const [authForm, setAuthForm] = useState({
    username: 'admin',
    password: 'admin123',
  });

  const [view, setView] = useState('dashboard');

  const isAdminLike = role === 'admin' || role === 'superadmin';
  const isAccessFullScreen = view === 'access-control';

  // ============================================================
  // DISPOSITIVOS ONLINE
  // ============================================================
  const onlineDevices = useMemo(
    () => devices.filter((d) => (d.status || '').toLowerCase() === 'online'),
    [devices]
  );

  const selected = useMemo(
    () => onlineDevices.find((d) => d.device_id === selectedId) || null,
    [onlineDevices, selectedId]
  );

  // ============================================================
  // CAMBIO DE VISTA
  // ============================================================
  const switchView = (next) => {
    setView(next);
    window.location.hash = next === 'dashboard' ? '' : next;
  };

  // ============================================================
  // LOGOUT
  // ============================================================
  const handleLogout = () => {
    setToken('');
    setRole('user');
    setDevices([]);
    setTelemetry([]);
    setSelectedId(null);
    setAuthError('');
    setView('dashboard');
    window.location.hash = '';
  };

  // ============================================================
  // LOGIN
  // ============================================================
  const handleLogin = async (e) => {
    e.preventDefault();
    setAuthError('');

    try {
      const { token: t, role: r } = await login(
        authForm.username,
        authForm.password
      );

      setToken(t);
      setRole(r);
    } catch (err) {
      console.error(err);
      setAuthError('Login failed');
      setToken('');
      setRole('user');
    }
  };

  // ============================================================
  // CARGA DE DISPOSITIVOS
  // ============================================================
  const refreshDevices = async () => {
    if (!token) return;

    try {
      const list = await fetchDevices(token);
      setDevices(list);

      const onlyOnline = list.filter(
        (d) => (d.status || '').toLowerCase() === 'online'
      );

      setSelectedId((prev) => {
        if (!onlyOnline.length) return null;
        if (!prev) return onlyOnline[0].device_id;

        return onlyOnline.some((d) => d.device_id === prev)
          ? prev
          : onlyOnline[0].device_id;
      });
    } catch (err) {
      console.error('Error refreshing devices:', err);
    }
  };

  // ============================================================
  // CARGA DE TELEMETRÍA
  // ============================================================
  const refreshTelemetry = async (deviceId) => {
    if (!token || !deviceId) return;

    try {
      const data = await fetchTelemetry(deviceId, token);
      setTelemetry(data);
    } catch (err) {
      console.error('Error refreshing telemetry:', err);
    }
  };

  // ============================================================
  // ENVÍO DE COMANDOS
  // ============================================================
  const handleSendCommand = async (cmd) => {
    if (!selectedId || !token) return;
    return sendCommand(selectedId, cmd, token);
  };

  // ============================================================
  // REFRESH AUTOMÁTICO DE DISPOSITIVOS
  // ============================================================
  useEffect(() => {
    if (!token) return;

    refreshDevices();
    const iv = setInterval(refreshDevices, 10000);

    return () => clearInterval(iv);
  }, [token]);

  // ============================================================
  // REFRESH AUTOMÁTICO DE TELEMETRÍA
  // ============================================================
  useEffect(() => {
    if (!token || !selectedId) {
      setTelemetry([]);
      return;
    }

    refreshTelemetry(selectedId);
    const iv = setInterval(() => refreshTelemetry(selectedId), 8000);

    return () => clearInterval(iv);
  }, [selectedId, token]);

  // ============================================================
  // LECTURA INICIAL DEL HASH
  // ============================================================
  useEffect(() => {
    const hash = window.location.hash.replace('#', '');

    if (
      hash === 'dashboard' ||
      hash === 'admin' ||
      hash === 'audit' ||
      hash === 'mqtt' ||
      hash === 'access-control'
    ) {
      setView(hash === 'dashboard' ? 'dashboard' : hash);
    } else {
      setView('dashboard');
    }
  }, []);

  // ============================================================
  // PANTALLA COMPLETA: CONTROL DE ACCESO
  // ============================================================
  if (token && isAccessFullScreen && isAdminLike) {
    return (
      <AccessControlPage
        token={token}
        username={authForm.username}
        onBack={() => switchView('dashboard')}
      />
    );
  }

  return (
    <div className="page">
      {/* ====================================================== */}
      {/* TOPBAR GENERAL */}
      {/* ====================================================== */}
      <header className="topbar">
        <div className="brand">
          <h1>Panel IoT</h1>

          <p className="muted">
            {token ? `Sesión: ${role}` : 'Sin sesión'}
          </p>
        </div>

        <form className="auth-inline" onSubmit={handleLogin}>
          <input
            className="text-input"
            placeholder="Usuario"
            value={authForm.username}
            onChange={(e) =>
              setAuthForm({ ...authForm, username: e.target.value })
            }
          />

          <input
            type="password"
            className="text-input"
            placeholder="Contraseña"
            value={authForm.password}
            onChange={(e) =>
              setAuthForm({ ...authForm, password: e.target.value })
            }
          />

          <button className="primary" type="submit">
            {token ? 'Refrescar token' : 'Ingresar'}
          </button>

          {token && (
            <button className="ghost" type="button" onClick={handleLogout}>
              Cerrar sesión
            </button>
          )}

          {authError && <p className="error">{authError}</p>}
        </form>
      </header>

      {/* ====================================================== */}
      {/* NAV GENERAL */}
      {/* ====================================================== */}
      {token && (
        <div className="nav-tabs">
          <button
            className={`tab ${view === 'dashboard' ? 'active' : ''}`}
            onClick={() => switchView('dashboard')}
          >
            Monitoreo
          </button>

          {isAdminLike && (
            <>
              <button
                className={`tab ${view === 'admin' ? 'active' : ''}`}
                onClick={() => switchView('admin')}
              >
                Usuarios
              </button>

              <button
                className={`tab ${view === 'audit' ? 'active' : ''}`}
                onClick={() => switchView('audit')}
              >
                Auditoría
              </button>

              <button
                className={`tab ${view === 'mqtt' ? 'active' : ''}`}
                onClick={() => switchView('mqtt')}
              >
                MQTT
              </button>

              <button
                className={`tab ${view === 'access-control' ? 'active' : ''}`}
                onClick={() => switchView('access-control')}
              >
                Control de Acceso
              </button>
            </>
          )}
        </div>
      )}

      {/* ====================================================== */}
      {/* VISTA DASHBOARD */}
      {/* ====================================================== */}
      {token && view === 'dashboard' && (
        <section className="content">
          <DeviceList
            devices={onlineDevices}
            onSelect={setSelectedId}
            selectedId={selectedId}
          />

          <DeviceDetail
            device={selected}
            telemetry={telemetry}
            onSendCommand={handleSendCommand}
            token={token}
            role={role}
          />
        </section>
      )}

      {/* ====================================================== */}
      {/* VISTAS ADMIN */}
      {/* ====================================================== */}
      {token && view === 'admin' && isAdminLike && (
        <AdminPanel token={token} role={role} />
      )}

      {token && view === 'audit' && isAdminLike && (
        <AuditPanel token={token} />
      )}

      {token && view === 'mqtt' && isAdminLike && (
        <MqttEventsPanel token={token} />
      )}
    </div>
  );
}