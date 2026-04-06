// dashboard/src/App.jsx

import { useEffect, useMemo, useState } from 'react';
import { fetchDevices, fetchTelemetry, login, sendCommand } from './api';
import DeviceList from './components/DeviceList';
import DeviceDetail from './components/DeviceDetail';
import StatusBadge from './components/StatusBadge';
import TerminalFeed from './components/TerminalFeed';
import AdminPanel from './components/AdminPanel';
import AuditPanel from './components/AuditPanel';
import MqttEventsPanel from './components/MqttEventsPanel';

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

  // ============================================================
  // DERIVADOS
  // ============================================================
  // Solo mostramos equipos online en el tablero principal.
  const onlineDevices = useMemo(
    () => devices.filter((d) => (d.status || '').toLowerCase() === 'online'),
    [devices]
  );

  const onlineCount = onlineDevices.length;

  // Dispositivo seleccionado actual, pero solo entre los online.
  const selected = useMemo(
    () => onlineDevices.find((d) => d.device_id === selectedId) || null,
    [onlineDevices, selectedId]
  );

  // ============================================================
  // CARGA DE DISPOSITIVOS
  // ============================================================
  const refreshDevices = async () => {
    try {
      const list = await fetchDevices(token);
      setDevices(list);

      const onlyOnline = list.filter(
        (d) => (d.status || '').toLowerCase() === 'online'
      );

      setSelectedId((prevSelectedId) => {
        if (!onlyOnline.length) return null;
        if (!prevSelectedId) return onlyOnline[0].device_id;

        const stillExistsOnline = onlyOnline.some(
          (d) => d.device_id === prevSelectedId
        );

        return stillExistsOnline ? prevSelectedId : onlyOnline[0].device_id;
      });
    } catch (err) {
      console.error('Error refreshing devices:', err);
    }
  };

  // ============================================================
  // CARGA DE TELEMETRÍA
  // ============================================================
  const refreshTelemetry = async (deviceId) => {
    if (!deviceId) return;

    try {
      const data = await fetchTelemetry(deviceId, token);
      setTelemetry(data);
    } catch (err) {
      console.error('Error refreshing telemetry:', err);
    }
  };

  // ============================================================
  // EFECTO: REFRESH DE DISPOSITIVOS
  // ============================================================
  useEffect(() => {
    if (!token) return;

    refreshDevices();
    const iv = setInterval(refreshDevices, 10000);

    return () => clearInterval(iv);
  }, [token]);

  // ============================================================
  // EFECTO: REFRESH DE TELEMETRÍA DEL DISPOSITIVO SELECCIONADO
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
      setAuthError(err?.response?.data?.detail || 'Login failed');
      setRole('user');
      setToken('');
    }
  };

  // ============================================================
  // LECTURA INICIAL DE HASH
  // ============================================================
  // Soporta:
  // - #admin
  // - #audit
  // - #mqtt
  useEffect(() => {
    const hash = window.location.hash.replace('#', '');

    if (hash === 'admin' || hash === 'audit' || hash === 'mqtt') {
      setView(hash);
    } else {
      setView('dashboard');
    }
  }, []);

  // ============================================================
  // CAMBIO DE VISTA
  // ============================================================
  const switchView = (next) => {
    setView(next);

    // Dashboard queda limpio sin hash.
    // Admin y audit usan hash para permitir acceso directo.
    window.location.hash = next === 'dashboard' ? '' : next;
  };

  // ============================================================
  // ENVÍO DE COMANDO
  // ============================================================
  const handleSendCommand = async (cmd) => {
    if (!selectedId) return;
    return sendCommand(selectedId, cmd, token);
  };

  return (
    <div className="page">
      {/* ====================================================== */}
      {/* TOPBAR */}
      {/* ====================================================== */}
      <header className="topbar">
        <div className="brand">
          <p className="eyebrow">Panel IoT</p>
          <h1>Monitoreo y Comandos</h1>

          {token ? (
            <p className="muted">
              Sesión: {role === 'admin' ? 'Administrador' : 'Usuario'}
            </p>
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
              onChange={(e) =>
                setAuthForm({ ...authForm, username: e.target.value })
              }
            />
          </div>

          <div>
            <label>Contraseña</label>
            <input
              type="password"
              className="text-input"
              value={authForm.password}
              onChange={(e) =>
                setAuthForm({ ...authForm, password: e.target.value })
              }
            />
          </div>

          <button className="primary" type="submit">
            {token ? 'Refrescar token' : 'Ingresar'}
          </button>

          {authError && <p className="error">{authError}</p>}
          {token && <p className="muted">Token activo</p>}
        </form>
      </header>

      {/* ====================================================== */}
      {/* HERO SIN LOGIN */}
      {/* ====================================================== */}
      {!token && (
        <section className="hero">
          <div>
            <p className="eyebrow">Plataforma IoT</p>
            <h2>Todo tu parque en un solo lugar.</h2>
            <p className="muted">
              Iniciá sesión para ver el tablero en vivo o administrar usuarios.
            </p>
          </div>
        </section>
      )}

      {/* ====================================================== */}
      {/* PESTAÑAS DE NAVEGACIÓN */}
      {/* ====================================================== */}
      {token && (
        <div className="nav-tabs">
          <button
            className={`tab ${view === 'dashboard' ? 'active' : ''}`}
            onClick={() => switchView('dashboard')}
          >
            Monitoreo
          </button>

          {role === 'admin' && (
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
                <span className="tab-label-desktop">Eventos MQTT</span>
                <span className="tab-label-mobile">Eventos</span>
              </button>
            </>
          )}
        </div>
      )}

      {/* ====================================================== */}
      {/* VISTA DASHBOARD */}
      {/* ====================================================== */}
      {token && view === 'dashboard' && (
        <>
          <section className="intro">
            <p className="muted">
              Supervisá tus dispositivos, enviá comandos y leé telemetría en segundos.
            </p>
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
              <h2>
                <StatusBadge status={token ? 'online' : 'offline'} />
              </h2>
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

      {/* ====================================================== */}
      {/* VISTA ADMINISTRACIÓN DE USUARIOS */}
      {/* ====================================================== */}
      {token && view === 'admin' && role === 'admin' && (
        <AdminPanel token={token} />
      )}

      {/* ====================================================== */}
      {/* VISTA AUDITORÍA */}
      {/* ====================================================== */}
      {token && view === 'audit' && role === 'admin' && (
        <AuditPanel token={token} />
      )}
      {/* ====================================================== */}
      {/* VISTA AUDITORÍA MQTT */}
      {/* ====================================================== */}
      {token && view === 'mqtt' && role === 'admin' && (
      <MqttEventsPanel token={token} />
      )}
    </div>
  );
}