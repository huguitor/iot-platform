import { useEffect, useMemo, useState } from 'react';
import { fetchAccessEvents, fetchDevices } from '../api';

// ============================================================
// HELPERS
// ============================================================
function toUnixTimestamp(value) {
  if (!value) return undefined;

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return undefined;

  return Math.floor(date.getTime() / 1000);
}

function formatDateTime(value) {
  if (!value) return '-';

  try {
    const date = new Date(value);

    if (Number.isNaN(date.getTime())) return '-';

    return date.toLocaleString('es-AR');
  } catch {
    return value || '-';
  }
}

// ============================================================
// ESTADO ACTUAL DE PUERTA
// Usa el estado persistente que viene desde devices.
// NO calcula el estado desde el histórico.
// ============================================================
function getDoorState(device) {
  if (
    !device ||
    device.last_door_opened === null ||
    device.last_door_opened === undefined
  ) {
    return '⚪ Sin dato';
  }

  const lastEventAt = device.last_door_event_at
    ? new Date(device.last_door_event_at).getTime()
    : null;

  if (!lastEventAt || Number.isNaN(lastEventAt)) {
    return '⚪ Sin dato';
  }

  const diffMinutes = (Date.now() - lastEventAt) / 1000 / 60;

  if (diffMinutes > 5) {
    return '⚪ Desactualizado';
  }

  return device.last_door_opened ? '🔴 Abierta' : '🟢 Cerrada';
}

// ============================================================
// COMPONENTE PRINCIPAL
// ============================================================
export default function AccessControlPanel({ token }) {
  const [events, setEvents] = useState([]);
  const [devices, setDevices] = useState([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [filters, setFilters] = useState({
    device_id: '',
    reader: '',
    card_uid: '',
    authorized: '',
    date_from: '',
    date_to: '',
    limit: 200,
  });

  // ============================================================
  // CARGA DE DATOS
  // - access_events: histórico auditable
  // - devices: estado actual persistido
  // ============================================================
  const loadData = async () => {
    if (!token) return;

    setLoading(true);
    setError('');

    try {
      const [eventsData, devicesData] = await Promise.all([
        fetchAccessEvents(token, {
          device_id: filters.device_id || undefined,
          reader: filters.reader || undefined,
          card_uid: filters.card_uid || undefined,
          authorized:
            filters.authorized === ''
              ? undefined
              : filters.authorized === 'true',
          date_from: toUnixTimestamp(filters.date_from),
          date_to: toUnixTimestamp(filters.date_to),
          limit: filters.limit,
        }),
        fetchDevices(token),
      ]);

      setEvents(eventsData);
      setDevices(devicesData);
    } catch (err) {
      console.error(err);
      setError('No se pudieron cargar los datos');
    } finally {
      setLoading(false);
    }
  };

  // Carga inicial
  useEffect(() => {
    if (!token) return;
    loadData();
  }, [token]);

  // ============================================================
  // TABLA 1: ESTADO ACTUAL
  // Fuente: devices
  // Usa:
  // - last_door_opened
  // - last_door_event_at
  // - last_door_reader
  // - last_card_uid
  // - last_access_authorized
  // ============================================================
  const currentStateRows = useMemo(() => {
    return devices.map((d) => {
      const state = getDoorState(d);

      return {
        id: d.device_id,
        created_at: d.last_door_event_at,
        device_id: d.device_id,
        reader: d.last_door_reader || '-',
        card_uid: d.last_card_uid || '-',
        authorized: d.last_access_authorized,
        door_label: state,
      };
    });
  }, [devices]);

  // ============================================================
  // TABLA 2: HISTÓRICO DE ACCESOS
  // Fuente: access_events
  // ============================================================
  const historyRows = useMemo(() => {
    return events.map((e) => ({
      id: e.id,
      created_at: e.created_at,
      device_id: e.device_id,
      reader: e.reader,
      card_uid: e.card_uid,
      authorized: e.authorized,
      door_label:
        e.door_opened === true
          ? '🔴 Abierta'
          : e.door_opened === false
            ? '🟢 Cerrada'
            : '⚪ Sin dato',
    }));
  }, [events]);

  return (
    <section className="audit-panel">
      <div className="audit-header">
        <div>
          <p className="eyebrow">Control de Acceso</p>

          <h2>Estado actual e histórico de puertas</h2>

          <p className="muted">
            Estado persistido en backend + histórico auditable de accesos
          </p>
        </div>

        <button className="primary" onClick={loadData} disabled={loading}>
          {loading ? 'Buscando...' : 'Buscar'}
        </button>
      </div>

      {/* ====================================================== */}
      {/* FILTROS DEL HISTÓRICO */}
      {/* ====================================================== */}
      <div className="mqtt-filters">
        <input
          className="text-input"
          placeholder="Dispositivo"
          value={filters.device_id}
          onChange={(e) =>
            setFilters((p) => ({ ...p, device_id: e.target.value }))
          }
        />

        <input
          className="text-input"
          placeholder="Lector"
          value={filters.reader}
          onChange={(e) =>
            setFilters((p) => ({ ...p, reader: e.target.value }))
          }
        />

        <input
          className="text-input"
          placeholder="UID tarjeta"
          value={filters.card_uid}
          onChange={(e) =>
            setFilters((p) => ({ ...p, card_uid: e.target.value }))
          }
        />

        <select
          className="text-input"
          value={filters.authorized}
          onChange={(e) =>
            setFilters((p) => ({ ...p, authorized: e.target.value }))
          }
        >
          <option value="">Todos</option>
          <option value="true">Autorizados</option>
          <option value="false">Denegados</option>
        </select>

        <input
          type="datetime-local"
          className="text-input"
          value={filters.date_from}
          onChange={(e) =>
            setFilters((p) => ({ ...p, date_from: e.target.value }))
          }
        />

        <input
          type="datetime-local"
          className="text-input"
          value={filters.date_to}
          onChange={(e) =>
            setFilters((p) => ({ ...p, date_to: e.target.value }))
          }
        />
      </div>

      {error && <p className="error">{error}</p>}

      {/* ====================================================== */}
      {/* TABLA 1 - ESTADO ACTUAL */}
      {/* ====================================================== */}
      <h3 className="access-section-title">Estado actual de puertas</h3>

      <div className="access-table-wrap">
        <table className="access-table">
          <thead>
            <tr>
              <th>Dispositivo</th>
              <th>Lector</th>
              <th>Última tarjeta</th>
              <th>Autorizado</th>
              <th>Puerta</th>
              <th>Último evento</th>
            </tr>
          </thead>

          <tbody>
            {currentStateRows.length === 0 ? (
              <tr>
                <td className="access-empty" colSpan="6">
                  {loading ? 'Cargando...' : 'Sin datos'}
                </td>
              </tr>
            ) : (
              currentStateRows.map((e) => (
                <tr key={e.id}>
                  <td className="access-device">{e.device_id || '-'}</td>
                  <td className="access-center">{e.reader || '-'}</td>
                  <td>{e.card_uid || '-'}</td>
                  <td className="access-center">
                    {e.authorized === true
                      ? '✔'
                      : e.authorized === false
                        ? '✖'
                        : '-'}
                  </td>
                  <td className="access-door">{e.door_label || '-'}</td>
                  <td>{formatDateTime(e.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ====================================================== */}
      {/* TABLA 2 - HISTÓRICO */}
      {/* ====================================================== */}
      <h3 className="access-section-title">Histórico de accesos</h3>

      <div className="access-table-wrap">
        <table className="access-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Dispositivo</th>
              <th>Lector</th>
              <th>Tarjeta</th>
              <th>Autorizado</th>
              <th>Puerta</th>
            </tr>
          </thead>

          <tbody>
            {historyRows.length === 0 ? (
              <tr>
                <td className="access-empty" colSpan="6">
                  {loading ? 'Cargando...' : 'Sin datos'}
                </td>
              </tr>
            ) : (
              historyRows.map((e) => (
                <tr key={e.id}>
                  <td>{formatDateTime(e.created_at)}</td>
                  <td className="access-device">{e.device_id || '-'}</td>
                  <td className="access-center">{e.reader || '-'}</td>
                  <td>{e.card_uid || '-'}</td>
                  <td className="access-center">
                    {e.authorized === true
                      ? '✔'
                      : e.authorized === false
                        ? '✖'
                        : '-'}
                  </td>
                  <td className="access-door">{e.door_label || '-'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}