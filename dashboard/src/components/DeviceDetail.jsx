import { useEffect, useMemo, useRef, useState } from 'react';
import StatusBadge from './StatusBadge';
import TelemetryChart from './TelemetryChart';
import { formatTime } from '../utils/time';

export default function DeviceDetail({ device, telemetry, onSendCommand, token, role }) {
  const [command, setCommand] = useState('{"cmd":"restart"}');
  const [selectedPreset, setSelectedPreset] = useState(null);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState('');

  const prevDeviceId = useRef(null);
  const isAdmin = role === 'admin';

  const latestPayload = useMemo(() => telemetry?.[0]?.payload || {}, [telemetry]);
  const payloadEntries = useMemo(() => Object.entries(latestPayload), [latestPayload]);

  const getValue = (key) => latestPayload?.[key];
  const getBool = (key) => Boolean(latestPayload?.[key]);

  useEffect(() => {
    if (device?.device_id !== prevDeviceId.current) {
      setCommand('{"cmd":"restart"}');
      setSelectedPreset(null);
      setMessage('');
      prevDeviceId.current = device?.device_id;
    }
  }, [device?.device_id]);

  if (!device) {
    return (
      <div className="panel">
        <p className="eyebrow">Dispositivo</p>
        <h3>No hay dispositivos online para mostrar</h3>
      </div>
    );
  }

  const handlePresetClick = (preset) => {
    setCommand(preset.body);
    setSelectedPreset(preset.label);
    setMessage('');
  };

  const handleCommandChange = (e) => {
    setCommand(e.target.value);
    setSelectedPreset(null);
  };

  const handleSend = async () => {
    setSending(true);
    setMessage('');
    try {
      await onSendCommand(command);
      setMessage('Comando encolado');
      setSelectedPreset(null);
    } catch (err) {
      setMessage(err?.response?.data?.detail || 'No autorizado para enviar comandos');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="detail-layout">
      <div className="panel">
        <div className="scada-header">
          <div>
            <p className="eyebrow">Dispositivo</p>
            <h2>{device.device_id}</h2>
            <p className="muted">{device.name || 'Sin nombre'}</p>
          </div>
          <StatusBadge status={device.status || 'unknown'} />
        </div>

        <div className="scada-stats">
          <StatCard
            label="Último mensaje"
            value={device.last_seen ? formatTime(device.last_seen) : 'n/d'}
          />
          <StatCard
            label="IP"
            value={getValue('ip') ?? 'n/d'}
          />
          <StatCard
            label="RSSI"
            value={getValue('rssi') ?? 'n/d'}
          />
          <StatCard
            label="Batería"
            value={getValue('batt') ?? 'n/d'}
          />
        </div>

        <div className="scada-io">
          <div>
            <p className="eyebrow">Entradas</p>
            <Indicator label="BTN1" active={getBool('btn1')} />
            <Indicator label="BTN2" active={getBool('btn2')} />
          </div>

          <div>
            <p className="eyebrow">Salidas</p>
            <Indicator label="OUT1" active={getBool('out1')} />
            <Indicator label="OUT2" active={getBool('out2')} />
            <Indicator label="SIRENA" active={getBool('sirena')} alarm />
          </div>
        </div>

        <div className="command-box">
          <div className="command-presets">
            <p className="eyebrow">Acciones rápidas</p>
            {!isAdmin && (
              <p className="muted">
                Visible solo como referencia. Tu usuario no tiene permisos para ejecutar comandos.
              </p>
            )}

            <div className="preset-grid">
              {presets.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className={`preset-btn ${selectedPreset === preset.label ? 'active' : ''}`}
                  onClick={() => handlePresetClick(preset)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>

          <div className="command-editor">
            <p className="eyebrow">Comando MQTT</p>
            <textarea
              className="text-area"
              value={command}
              onChange={handleCommandChange}
              rows={4}
              placeholder='{"cmd":"restart"}'
            />
            <div className="command-actions">
              <span className="muted">Topic: iot/devices/{device.device_id}/cmd</span>
              <button
                className="primary"
                type="button"
                onClick={handleSend}
                disabled={sending}
              >
                {sending ? 'Enviando...' : 'Enviar'}
              </button>
            </div>
            {message && <p className="muted">{message}</p>}
          </div>
        </div>

        {isAdmin && payloadEntries.length > 0 && (
          <div className="kv-grid">
            <p className="eyebrow">Variables actuales</p>
            {payloadEntries.map(([key, value]) => (
              <div key={key} className="kv-row">
                <span className="kv-key">{key}</span>
                <span className="kv-val">{formatValue(value)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <TelemetryChart telemetry={telemetry} />
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Indicator({ label, active, alarm = false }) {
  return (
    <div className={`indicator ${active ? 'on' : ''} ${alarm ? 'alarm' : ''}`}>
      <span className="dot"></span>
      <span>{label}</span>
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