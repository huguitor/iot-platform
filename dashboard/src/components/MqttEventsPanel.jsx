// dashboard/src/components/MqttEventsPanel.jsx

import { useEffect, useMemo, useState } from 'react';
import { fetchMqttEvents } from '../api';
import { formatDateTime } from '../utils/time';

function toUnixTimestamp(value) {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return Math.floor(date.getTime() / 1000);
}


function buildPayloadSummary(payload) {
  if (!payload || typeof payload !== 'object') return 'Sin datos';

  const preferred = [
    ['sirena', 'sirena'],
    ['out1', 'out1'],
    ['out2', 'out2'],
    ['batt', 'batt'],
    ['rssi', 'rssi'],
    ['btn1', 'btn1'],
    ['btn2', 'btn2'],
  ];

  const parts = preferred
    .filter(([key]) => payload[key] !== undefined)
    .map(([key, label]) => `${label}=${payload[key]}`);

  if (parts.length > 0) {
    return parts.join(' · ');
  }

  const entries = Object.entries(payload).slice(0, 5);
  if (entries.length === 0) return 'Sin datos';

  return entries.map(([k, v]) => `${k}=${v}`).join(' · ');
}

export default function MqttEventsPanel({ token }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState(null);

  const [filters, setFilters] = useState({
    device_id: '',
    date_from: '',
    date_to: '',
    event_type: '',
    limit: 200,
  });

  const loadEvents = async () => {
    if (!token) {
      setError('No hay token de autenticación');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const data = await fetchMqttEvents(token, {
        device_id: filters.device_id || undefined,
        date_from: toUnixTimestamp(filters.date_from),
        date_to: toUnixTimestamp(filters.date_to),
        event_type: filters.event_type || undefined,
        limit: filters.limit || 200,
      });

      setEvents(data);
    } catch (err) {
      console.error('Error loading MQTT events:', err);
      console.error('Backend response:', err?.response?.data);
      setError('No se pudieron cargar los eventos MQTT');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!token) return;
    loadEvents();
  }, [token]);

  const desktopEvents = useMemo(() => events, [events]);

  return (
    <section className="audit-panel">
      <div className="audit-header mqtt-header">
        <div>
          <p className="eyebrow">Eventos MQTT</p>
          <h2>Histórico técnico</h2>
          <p className="muted">
            Buscá eventos por tiempo y revisá rápido qué pasó en el dispositivo.
          </p>
        </div>

        <button className="primary" onClick={loadEvents} disabled={loading}>
          {loading ? 'Buscando...' : 'Buscar'}
        </button>
      </div>

      <div className="mqtt-filters">
        <div className="filter-field">
          <label>Dispositivo</label>
          <input
            className="text-input"
            placeholder="Ej: mi-device"
            value={filters.device_id}
            onChange={(e) =>
              setFilters((prev) => ({ ...prev, device_id: e.target.value }))
            }
          />
        </div>

        <div className="filter-field">
          <label>Desde</label>
          <input
            className="text-input mqtt-datetime"
            type="datetime-local"
            value={filters.date_from}
            onChange={(e) =>
              setFilters((prev) => ({ ...prev, date_from: e.target.value }))
            }
          />
        </div>

        <div className="filter-field">
          <label>Hasta</label>
          <input
            className="text-input mqtt-datetime"
            type="datetime-local"
            value={filters.date_to}
            onChange={(e) =>
              setFilters((prev) => ({ ...prev, date_to: e.target.value }))
            }
          />
        </div>

        <div className="filter-field">
          <label>Tipo de evento</label>
          <input
            className="text-input"
            placeholder="Ej: mqtt_state_received"
            value={filters.event_type}
            onChange={(e) =>
              setFilters((prev) => ({ ...prev, event_type: e.target.value }))
            }
          />
        </div>

        <div className="filter-field">
          <label>Límite</label>
          <input
            className="text-input"
            type="number"
            min="1"
            max="5000"
            value={filters.limit}
            onChange={(e) =>
              setFilters((prev) => ({
                ...prev,
                limit: Number(e.target.value) || 200,
              }))
            }
          />
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {/* Desktop / tablet */}
      <div className="audit-table-wrap mqtt-desktop-view">
        <table className="audit-table">
          <thead>
            <tr>
              <th>Fecha / Hora</th>
              <th>Dispositivo</th>
              <th>Dirección</th>
              <th>Tipo</th>
              <th>Topic</th>
              <th>OK</th>
              <th>Payload</th>
            </tr>
          </thead>

          <tbody>
            {desktopEvents.length === 0 ? (
              <tr>
                <td colSpan="7" className="muted">
                  {loading ? 'Cargando...' : 'No hay eventos para mostrar'}
                </td>
              </tr>
            ) : (
              desktopEvents.map((event) => (
                <tr key={event.id}>
                  <td>{formatDateTime(event.created_at)}</td>
                  <td>{event.device_id || '-'}</td>
                  <td>{event.direction || '-'}</td>
                  <td>{event.event_type || '-'}</td>
                  <td>{event.topic || '-'}</td>
                  <td>{event.processed_ok ? 'Sí' : 'No'}</td>
                  <td>
                    <pre className="audit-payload">
                      {event.payload
                        ? JSON.stringify(event.payload, null, 2)
                        : '-'}
                    </pre>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile */}
      <div className="mqtt-mobile-view">
        {events.length === 0 ? (
          <div className="mqtt-empty muted">
            {loading ? 'Cargando...' : 'No hay eventos para mostrar'}
          </div>
        ) : (
          <div className="mqtt-card-list">
            {events.map((event) => {
              const isExpanded = expandedId === event.id;
              const summary = buildPayloadSummary(event.payload);

              return (
                <article className="mqtt-card" key={event.id}>
                  <div className="mqtt-card-top">
                    <p className="mqtt-card-time">{formatDateTime(event.created_at)}</p>
                    <button
                      className="ghost mqtt-detail-btn"
                      type="button"
                      onClick={() =>
                        setExpandedId((prev) => (prev === event.id ? null : event.id))
                      }
                    >
                      {isExpanded ? 'Ocultar' : 'Detalle'}
                    </button>
                  </div>

                  <p className="mqtt-card-summary">{summary}</p>

                  {isExpanded && (
                    <div className="mqtt-card-detail">
                      <div className="kv-grid">
                        <div className="kv-row">
                          <span className="kv-key">Dispositivo</span>
                          <span className="kv-val">{event.device_id || '-'}</span>
                        </div>
                        <div className="kv-row">
                          <span className="kv-key">Dirección</span>
                          <span className="kv-val">{event.direction || '-'}</span>
                        </div>
                        <div className="kv-row">
                          <span className="kv-key">Tipo</span>
                          <span className="kv-val">{event.event_type || '-'}</span>
                        </div>
                        <div className="kv-row">
                          <span className="kv-key">Topic</span>
                          <span className="kv-val">{event.topic || '-'}</span>
                        </div>
                        <div className="kv-row">
                          <span className="kv-key">Procesado</span>
                          <span className="kv-val">{event.processed_ok ? 'Sí' : 'No'}</span>
                        </div>
                      </div>

                      <pre className="audit-payload mqtt-mobile-payload">
                        {event.payload
                          ? JSON.stringify(event.payload, null, 2)
                          : '-'}
                      </pre>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}