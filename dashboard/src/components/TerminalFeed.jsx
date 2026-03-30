import React from 'react';

const timeZone = import.meta.env.VITE_TIME_ZONE || Intl.DateTimeFormat().resolvedOptions().timeZone;

export default function TerminalFeed({ telemetry, device }) {
  const items = (telemetry || []).slice(0, 12);
  return (
    <div className="panel terminal">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Terminal</p>
          <h3>Mensajes crudos</h3>
        </div>
        <span className="chip">{device?.device_id || 'sin dispositivo'}</span>
      </div>
      <div className="terminal-body">
        {items.length === 0 && <p className="muted">Sin mensajes aún.</p>}
        {items.map((t) => (
          <div key={`${t.id}-${t.timestamp}`} className="terminal-line">
            <span className="terminal-time">{new Date(t.timestamp).toLocaleTimeString('es-AR', { hour12: false, timeZone })}</span>
            <span className="terminal-text">{JSON.stringify(t.payload)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
