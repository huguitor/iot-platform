import { useMemo, useState } from 'react';
import StatusBadge from './StatusBadge';
import TelemetryChart from './TelemetryChart';

export default function DeviceDetail({ device, telemetry, onSendCommand, token }) {
  const [command, setCommand] = useState('restart');
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState('');

  const latestPayload = useMemo(() => telemetry?.[0]?.payload || {}, [telemetry]);
  const payloadEntries = useMemo(() => Object.entries(latestPayload), [latestPayload]);

  if (!device) {
    return (
      <div className="panel">
        <p className="eyebrow">Dispositivo</p>
        <h3>Selecciona un dispositivo</h3>
      </div>
    );
  }

  const handleSend = async () => {
    setSending(true);
    setMessage('');
    try {
      await onSendCommand(command);
      setMessage('Comando encolado');
    } catch (err) {
      setMessage(err?.response?.data?.detail || 'No se pudo enviar');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="detail-layout">
      <div className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Dispositivo</p>
            <h3>{device.device_id}</h3>
            <p className="device-name">{device.name || 'Sin nombre'}</p>
          </div>
          <StatusBadge status={device.status || 'unknown'} />
        </div>
        <div className="stats-grid">
          <div className="stat-card">
            <p className="eyebrow">Último mensaje</p>
            <h4>{device.last_seen ? new Date(device.last_seen).toLocaleString() : 'n/d'}</h4>
          </div>
          <div className="stat-card">
            <p className="eyebrow">Creado</p>
            <h4>{device.created_at ? new Date(device.created_at).toLocaleDateString() : 'n/d'}</h4>
          </div>
        </div>
        <div className="command-box">
          <div className="command-presets">
            <p className="eyebrow">Comandos rápidos</p>
            <div className="preset-grid">
              {presets.map((p) => (
                <button
                  key={p.label}
                  className="preset-btn"
                  onClick={() => setCommand(p.body)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="command-editor">
            <p className="eyebrow">Comando MQTT</p>
            <textarea
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              className="text-area"
              rows={3}
              placeholder='{"cmd":"restart"}'
            />
            <div className="command-actions">
              <span className="muted">Topic: iot/devices/{device.device_id}/command</span>
              <button className="primary" onClick={handleSend} disabled={!token || sending}>
                {sending ? 'Enviando...' : 'Enviar'}
              </button>
            </div>
            {message && <p className="muted">{message}</p>}
          </div>
        </div>

        {payloadEntries.length > 0 && (
          <div className="kv-grid">
            <p className="eyebrow">Datos recientes</p>
            {payloadEntries.map(([k, v]) => (
              <div key={k} className="kv-row">
                <span className="kv-key">{k}</span>
                <span className="kv-val">{formatValue(v)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <TelemetryChart telemetry={telemetry} />
    </div>
  );
}

function formatValue(value) {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const presets = [
  { label: 'Reiniciar', body: '{"cmd":"restart"}' },
  { label: 'Sirena 1s', body: '{"cmd":"sirena","value":true}' },
  { label: 'Encender R1', body: '{"cmd":"set_output","output":1,"value":true}' },
  { label: 'Apagar R1', body: '{"cmd":"set_output","output":1,"value":false}' },
  { label: 'Encender R2', body: '{"cmd":"set_output","output":2,"value":true}' },
  { label: 'Apagar R2', body: '{"cmd":"set_output","output":2,"value":false}' },
];
