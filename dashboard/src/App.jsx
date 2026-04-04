// dashboard/src/App.jsx

import { useEffect, useMemo, useState } from 'react';
import { fetchDevices, fetchTelemetry, login, sendCommand } from './api';
import DeviceList from './components/DeviceList';
import DeviceDetail from './components/DeviceDetail';
import StatusBadge from './components/StatusBadge';
import TerminalFeed from './components/TerminalFeed';
import AdminPanel from './components/AdminPanel';

export default function App() {
  const [devices, setDevices] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const [token, setToken] = useState('');
  const [role, setRole] = useState('user');
  const [authError, setAuthError] = useState('');
  const [authForm, setAuthForm] = useState({ username: 'admin', password: 'admin123' });
  const [view, setView] = useState('dashboard');

  const onlineDevices = useMemo(
    () => devices.filter((d) => (d.status || '').toLowerCase() === 'online'),
    [devices]
  );

  const onlineCount = onlineDevices.length;

  const selected = useMemo(
    () => onlineDevices.find((d) => d.device_id === selectedId) || null,
    [onlineDevices, selectedId]
  );

  const refreshDevices = async () => {
    try {
      const list = await fetchDevices(token);
      setDevices(list);

      const onlyOnline = list.filter((d) => (d.status || '').toLowerCase() === 'online');

      setSelectedId((prevSelectedId) => {
        if (!onlyOnline.length) return null;
        if (!prevSelectedId) return onlyOnline[0].device_id;

        const stillExistsOnline = onlyOnline.some((d) => d.device_id === prevSelectedId);
        return stillExistsOnline ? prevSelectedId : onlyOnline[0].device_id;
      });
    } catch (err) {
      console.error(err);
    }
  };

  const refreshTelemetry = async (deviceId) => {
    if (!deviceId) return;
    try {
      const data = await fetchTelemetry(deviceId, token);
      setTelemetry(data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    refreshDevices();
    const iv = setInterval(refreshDevices, 10000);
    return () => clearInterval(iv);
  }, [token]);

  useEffect(() => {
    if (!selectedId) {
      setTelemetry([]);
      return;
    }

    refreshTelemetry(selectedId);
    const iv = setInterval(() => refreshTelemetry(selectedId), 8000);

    return () => clearInterval(iv);
  }, [selectedId, token]);

  const handleLogin = async (e) => {
    e.preventDefault();
    setAuthError('');
    try {
      const { token: t, role: r } = await login(authForm.username, authForm.password);
      setToken(t);
      setRole(r);
    } catch (err) {
      setAuthError(err?.response?.data?.detail || 'Login failed');
      setRole('user');
    }
  };

  useEffect(() => {
    const hash = window.location.hash.replace('#', '');
    if (hash === 'admin') setView('admin');
  }, []);

  const switchView = (next) => {
    setView(next);
    window.location.hash = next === 'admin' ? 'admin' : '';
  };

  const handleSendCommand = async (cmd) => {
    if (!selectedId) return;
    return sendCommand(selectedId, cmd, token);
  };

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <p className="eyebrow">Panel IoT</p>
          <h1>Monitoreo y Comandos</h1>
          {token ? (
            <p className="muted">Sesión: {role === 'admin' ? 'Administrador' : 'Usuario'}</p>
          ) : (
            <p className="muted">Ingresá para ver los dispositivos</p>
          )}
        </div>

        <form className="auth-inline" onSubmit={handleLogin}>
          <div>
            <label>Usuario</label>
            <input
              className="text-input"
              value={authForm.username}
              onChange={(e) => setAuthForm({ ...authForm, username: e.target.value })}
            />
          </div>
          <div>
            <label>Contraseña</label>
            <input
              type="password"
              className="text-input"
              value={authForm.password}
              onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })}
            />
          </div>
          <button className="primary" type="submit">
            {token ? 'Refrescar token' : 'Ingresar'}
          </button>
          {authError && <p className="error">{authError}</p>}
          {token && <p className="muted">Token activo</p>}
        </form>
      </header>

      {!token && (
        <section className="hero">
          <div>
            <p className="eyebrow">Plataforma IoT</p>
            <h2>Todo tu parque en un solo lugar.</h2>
            <p className="muted">Iniciá sesión para ver el tablero en vivo o administrar usuarios.</p>
          </div>
        </section>
      )}

      {token && (
        <div className="nav-tabs">
          <button
            className={`tab ${view === 'dashboard' ? 'active' : ''}`}
            onClick={() => switchView('dashboard')}
          >
            Monitoreo
          </button>
          {role === 'admin' && (
            <button
              className={`tab ${view === 'admin' ? 'active' : ''}`}
              onClick={() => switchView('admin')}
            >
              Usuarios
            </button>
          )}
        </div>
      )}

      {token && view === 'dashboard' && (
        <>
          <section className="intro">
            <p className="muted">Supervisá tus dispositivos, envía comandos y lee telemetría en segundos.</p>
          </section>

          <section className="metrics">
            <div className="metric-card">
              <p className="eyebrow">En línea</p>
              <h2>{onlineCount}</h2>
              <p className="muted">Dispositivos reportando</p>
            </div>
            <div className="metric-card">
              <p className="eyebrow">Flota visible</p>
              <h2>{onlineDevices.length}</h2>
              <p className="muted">Solo equipos online</p>
            </div>
            <div className="metric-card">
              <p className="eyebrow">API</p>
              <h2><StatusBadge status={token ? 'online' : 'offline'} /></h2>
              <p className="muted">Estado del token</p>
            </div>
          </section>

          <section className="content">
            <DeviceList
              devices={onlineDevices}
              onSelect={setSelectedId}
              selectedId={selectedId}
            />

            <div className="detail-stack">
              <DeviceDetail
                device={selected}
                telemetry={telemetry}
                onSendCommand={handleSendCommand}
                token={token}
                role={role}
              />

              {role === 'admin' && (
                <TerminalFeed device={selected} telemetry={telemetry} />
              )}
            </div>
          </section>
        </>
      )}

      {token && view === 'admin' && role === 'admin' && <AdminPanel token={token} />}
    </div>
  );
}