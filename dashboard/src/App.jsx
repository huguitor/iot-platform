import { useEffect, useMemo, useState } from 'react';
import { fetchDevices, fetchTelemetry, login, sendCommand } from './api';
import DeviceList from './components/DeviceList';
import DeviceDetail from './components/DeviceDetail';
import StatusBadge from './components/StatusBadge';
import TerminalFeed from './components/TerminalFeed';

export default function App() {
  const [devices, setDevices] = useState([]);
  const [selected, setSelected] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const [token, setToken] = useState('');
  const [authError, setAuthError] = useState('');
  const [authForm, setAuthForm] = useState({ username: 'admin', password: 'admin123' });

  const onlineCount = useMemo(() => devices.filter((d) => (d.status || '').toLowerCase() === 'online').length, [devices]);

  const refreshDevices = async () => {
    try {
      const list = await fetchDevices(token);
      setDevices(list);
      if (!selected && list.length > 0) {
        setSelected(list[0]);
      }
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
    if (selected?.device_id) {
      refreshTelemetry(selected.device_id);
      const iv = setInterval(() => refreshTelemetry(selected.device_id), 8000);
      return () => clearInterval(iv);
    }
    return undefined;
  }, [selected, token]);

  const handleLogin = async (e) => {
    e.preventDefault();
    setAuthError('');
    try {
      const t = await login(authForm.username, authForm.password);
      setToken(t);
    } catch (err) {
      setAuthError(err?.response?.data?.detail || 'Login failed');
    }
  };

  const handleSendCommand = async (cmd) => {
    if (!selected) return;
    return sendCommand(selected.device_id, cmd, token);
  };

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <p className="eyebrow">Panel IoT</p>
          <h1>Monitoreo y Comandos</h1>
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
          <button className="primary" type="submit">{token ? 'Refrescar token' : 'Ingresar'}</button>
          {authError && <p className="error">{authError}</p>}
          {token && <p className="muted">Token activo</p>}
        </form>
      </header>

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
          <p className="eyebrow">Flota</p>
          <h2>{devices.length}</h2>
          <p className="muted">Total de dispositivos</p>
        </div>
        <div className="metric-card">
          <p className="eyebrow">API</p>
          <h2><StatusBadge status={token ? 'online' : 'offline'} /></h2>
          <p className="muted">Estado del token</p>
        </div>
      </section>

      <section className="content">
        <DeviceList
          devices={devices}
          onSelect={(d) => setSelected(d)}
          selectedId={selected?.device_id}
        />
        <div className="detail-stack">
          <DeviceDetail
            device={selected}
            telemetry={telemetry}
            onSendCommand={handleSendCommand}
            token={token}
          />
          <TerminalFeed device={selected} telemetry={telemetry} />
        </div>
      </section>
    </div>
  );
}
