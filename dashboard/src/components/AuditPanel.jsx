// dashboard/src/components/AuditPanel.jsx

import { useEffect, useState } from "react";
import { fetchAuditLogs } from "../api";

export default function AuditPanel({ token }) {
  const [logs, setLogs] = useState([]);
  const [filters, setFilters] = useState({
    user: "",
    device: "",
    action: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadLogs = async () => {
    if (!token) {
      setError("No hay token de autenticación");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const data = await fetchAuditLogs(token, {
        limit: 100,
        user: filters.user,
        device: filters.device,
        action: filters.action,
      });

      setLogs(data);
    } catch (err) {
      console.error("Error loading audit logs:", err);
      console.error("Backend response:", err?.response?.data);
      setError("No se pudieron cargar los logs de auditoría");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!token) return;

    loadLogs();

    const interval = setInterval(loadLogs, 5000);
    return () => clearInterval(interval);
  }, [token, filters.user, filters.device, filters.action]);

  const getActionClass = (actionType = "") => {
    const t = actionType.toLowerCase();

    if (t.includes("failed") || t.includes("denied") || t.includes("error")) {
      return "audit-badge audit-badge-danger";
    }

    if (t.includes("login")) {
      return "audit-badge audit-badge-info";
    }

    if (t.includes("command")) {
      return "audit-badge audit-badge-warning";
    }

    if (t.includes("mqtt")) {
      return "audit-badge audit-badge-success";
    }

    return "audit-badge audit-badge-neutral";
  };

  return (
    <section className="audit-panel">
      <div className="audit-header">
        <div>
          <p className="eyebrow">Auditoría</p>
          <h2>Eventos del sistema</h2>
          <p className="muted">
            Consultá accesos, comandos, eventos MQTT y actividad operativa.
          </p>
        </div>

        <button className="primary" onClick={loadLogs} disabled={loading}>
          {loading ? "Actualizando..." : "Actualizar"}
        </button>
      </div>

      <div className="audit-filters">
        <input
          className="text-input"
          placeholder="Filtrar por usuario"
          value={filters.user}
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, user: e.target.value }))
          }
        />

        <input
          className="text-input"
          placeholder="Filtrar por dispositivo"
          value={filters.device}
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, device: e.target.value }))
          }
        />

        <input
          className="text-input"
          placeholder="Filtrar por acción"
          value={filters.action}
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, action: e.target.value }))
          }
        />
      </div>

      {error && <p className="error">{error}</p>}

      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th>Fecha / Hora</th>
              <th>Usuario</th>
              <th>Dispositivo</th>
              <th>Acción</th>
              <th>Resultado</th>
              <th>IP</th>
              <th>Payload</th>
            </tr>
          </thead>

          <tbody>
            {logs.length === 0 ? (
              <tr>
                <td colSpan="7" className="muted">
                  {loading ? "Cargando..." : "No hay eventos para mostrar"}
                </td>
              </tr>
            ) : (
              logs.map((log) => (
                <tr key={log.id}>
                  <td>{new Date(log.created_at).toLocaleString("es-AR")}</td>
                  <td>{log.username || "-"}</td>
                  <td>{log.device_id || "-"}</td>
                  <td>
                    <span className={getActionClass(log.action_type)}>
                      {log.action_type}
                    </span>
                  </td>
                  <td>{log.result || "-"}</td>
                  <td>{log.ip_address || "-"}</td>
                  <td>
                    <pre className="audit-payload">
                      {log.payload
                        ? JSON.stringify(log.payload, null, 2)
                        : "-"}
                    </pre>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}