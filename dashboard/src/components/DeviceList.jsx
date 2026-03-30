import StatusBadge from './StatusBadge';

export default function DeviceList({ devices, onSelect, selectedId }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Dispositivos</p>
          <h3>Flota ({devices.length})</h3>
        </div>
      </div>
      <div className="device-grid">
        {devices.map((d) => (
          <button
            key={d.device_id}
            className={`device-card ${selectedId === d.device_id ? 'active' : ''}`}
            onClick={() => onSelect(d)}
          >
            <div className="device-card__top">
              <span className="device-id">{d.device_id}</span>
              <StatusBadge status={d.status || 'unknown'} />
            </div>
            <p className="device-name">{d.name || 'Sin nombre'}</p>
            <p className="device-meta">Último mensaje: {d.last_seen ? new Date(d.last_seen).toLocaleString() : 'n/d'}</p>
          </button>
        ))}
      </div>
    </div>
  );
}
