import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchAccessEvents, fetchDevices } from '../api';

// ============================================================
// HELPERS
// ============================================================

/**
 * Convierte un string datetime-local a Unix timestamp (segundos).
 * Retorna undefined si el valor es inválido o vacío.
 */
function toUnixTimestamp(value) {
  if (!value) return undefined;

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return undefined;

  return Math.floor(date.getTime() / 1000);
}

/**
 * Formatea una fecha/hora en locale es-AR.
 * Retorna '-' si el valor es inválido o vacío.
 */
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
// RESULTADO VISUAL DEL EVENTO
// Mapea event_type + payload a una cadena legible con ícono.
// ============================================================
function buildVisualResult(event) {
  const payload = event.payload || {};
  const eventType = event.event_type;

  // ----------------------------------------------------------
  // ACCESS
  // ----------------------------------------------------------
  if (eventType === 'access') {
    if (event.authorized === true) return '✔ Acceso autorizado';
    if (event.authorized === false) return '✖ Acceso denegado';
    return '⚪ Evento access';
  }

  // ----------------------------------------------------------
  // BUTTON
  // ----------------------------------------------------------
  if (eventType === 'button') {
    return '✔ Apertura por pulsador';
  }

  // ----------------------------------------------------------
  // DOOR
  // ----------------------------------------------------------
  if (eventType === 'door') {
    const state = payload.state;
    if (state === 'OPEN') return '🔴 Puerta abierta';
    if (state === 'CLOSED') return '🟢 Puerta cerrada';
    return '⚪ Estado puerta desconocido';
  }

  // ----------------------------------------------------------
  // MODE
  // ----------------------------------------------------------
  if (eventType === 'mode') {
    const mode = payload.mode;
    if (mode === 'PROGRAM') return '🟡 Modo programación habilitado';
    if (mode === 'READ:MANUAL') return '⚪ Modo lectura habilitado';
    if (mode === 'READ:TIMEOUT') return '⚪ Lectura finalizada por timeout';
    return `⚪ Mode ${mode || '-'}`;
  }

  // ----------------------------------------------------------
  // ENROLL
  // ----------------------------------------------------------
  if (eventType === 'enroll') {
    const result = payload.result;
    if (result === 'OK') return '✔ Tarjeta registrada';
    if (result === 'EXISTS') return '⚠ Tarjeta duplicada';
    if (result === 'FULL') return '⚠ Memoria llena';
    return `⚪ Enroll ${result || '-'}`;
  }

  // ----------------------------------------------------------
  // DELETE
  // ----------------------------------------------------------
  if (eventType === 'delete') {
    const result = payload.result;
    if (result === 'OK') return '✔ Tarjeta eliminada';
    if (result === 'NOT_FOUND') return '⚠ Tarjeta inexistente';
    if (result === 'FIXED') return '⚠ Tarjeta protegida';
    return `⚪ Delete ${result || '-'}`;
  }

  // ----------------------------------------------------------
  // ERROR
  // ----------------------------------------------------------
  if (eventType === 'error') {
    const err = payload.error || 'UNKNOWN';
    return `⚠ Error ${err}`;
  }

  // ----------------------------------------------------------
  // RESET
  // ----------------------------------------------------------
  if (eventType === 'reset') {
    return '⚠ Memoria reiniciada';
  }

  // ----------------------------------------------------------
  // LIST
  // ----------------------------------------------------------
  if (eventType === 'list') {
    return '📋 Lectura de tarjetas';
  }

  return `⚪ ${eventType || 'Evento desconocido'}`;
}

// ============================================================
// ESTADO ACTUAL DE PUERTA
// Lee el estado persistido en el objeto device, NO lo calcula
// desde el histórico de eventos.
// ============================================================
function getDoorState(device) {
  if (!device) return '⚪ Sin dato';

  const sensorState = device.last_door_sensor_state;

  if (!sensorState) return '⚪ Sin sensor';

  const lastEventAt = device.last_door_sensor_at
    ? new Date(device.last_door_sensor_at).getTime()
    : null;

  if (!lastEventAt || Number.isNaN(lastEventAt)) return '⚪ Sin dato';

  const diffMinutes = (Date.now() - lastEventAt) / 1000 / 60;

  // Más de 5 minutos sin actualización → dato obsoleto
  if (diffMinutes > 5) return '⚪ Desactualizado';

  if (sensorState === 'open') return '🔴 Abierta';
  if (sensorState === 'closed') return '🟢 Cerrada';

  return `⚪ ${sensorState}`;
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

  // ----------------------------------------------------------
  // loadData memoizada con useCallback.
  // Se regenera solo cuando cambian token o filters, lo que
  // hace que el useEffect del intervalo detecte el cambio,
  // limpie el intervalo anterior y arranque uno nuevo con la
  // función actualizada → el auto-refresh siempre usa los
  // filtros vigentes sin necesitar refs manuales.
  // ----------------------------------------------------------
  const loadData = useCallback(async () => {
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
  }, [token, filters]);

  // ----------------------------------------------------------
  // Carga inicial + auto-refresh cada 5 s.
  // [token, loadData] como deps: cuando loadData cambia (por
  // cambio de token o filters) el efecto se re-ejecuta,
  // limpia el intervalo viejo y crea uno nuevo actualizado.
  // ----------------------------------------------------------
  useEffect(() => {
    if (!token) return;

    loadData();

    const intervalId = window.setInterval(() => {
      loadData();
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [token, loadData]);

  // ============================================================
  // TABLA 1: ESTADO ACTUAL
  // Fuente: devices (estado persistido en backend)
  // ============================================================
  const currentStateRows = useMemo(() => {
    return devices
      .filter(
        (d) =>
          d.last_door_sensor_state !== null &&
          d.last_door_sensor_state !== undefined
      )
      .map((d) => ({
        id: d.device_id,
        created_at: d.last_door_sensor_at,
        device_id: d.device_id,
        reader: d.last_door_reader || '-',
        door_label: getDoorState(d),
      }));
  }, [devices]);

  // ============================================================
  // TABLA 2: HISTÓRICO DE ACCESOS
  // Fuente: access_events (log auditable)
  // ============================================================
  const historyRows = useMemo(() => {
    return events.map((e) => ({
      id: e.id,
      created_at: e.created_at,
      device_id: e.device_id,
      reader: e.reader,
      event_type: e.event_type,
      card_uid: e.card_uid || '-',
      visual_result: buildVisualResult(e),
    }));
  }, [events]);

  // ============================================================
  // RENDER
  // ============================================================
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
      {/* FILTROS DEL HISTÓRICO                                   */}
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
      {/* TABLA 1 — ESTADO ACTUAL                                 */}
      {/* 4 columnas → colSpan="4"                               */}
      {/* ====================================================== */}
      <h3 className="access-section-title">Estado actual de puertas</h3>

      <div className="access-table-wrap">
        <table className="access-table">
          <thead>
            <tr>
              <th>Device ID</th>
              <th>Reader</th>
              <th>Estado físico</th>
              <th>Última actualización</th>
            </tr>
          </thead>

          <tbody>
            {currentStateRows.length === 0 ? (
              <tr>
                <td className="access-empty" colSpan="4">
                  {loading ? 'Cargando...' : 'Sin datos'}
                </td>
              </tr>
            ) : (
              currentStateRows.map((e) => (
                <tr key={e.id}>
                  <td className="access-device">{e.device_id || '-'}</td>
                  <td className="access-center">{e.reader || '-'}</td>
                  <td className="access-door">  {e.door_label || '-'}</td>
                  <td>{formatDateTime(e.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ====================================================== */}
      {/* TABLA 2 — HISTÓRICO                                     */}
      {/* 6 columnas → colSpan="6"                               */}
      {/* ====================================================== */}
      <h3 className="access-section-title">Histórico de accesos</h3>

      <div className="access-table-wrap">
        <table className="access-table">
          <thead>
            <tr>
              <th>Fecha servidor</th>
              <th>Device ID</th>
              <th>Reader</th>
              <th>Event type</th>
              <th>Card UID</th>
              <th>Resultado visual</th>
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
                  <td className="access-center">{e.event_type || '-'}</td>
                  <td>{e.card_uid || '-'}</td>
                  <td className="access-door">{e.visual_result}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
