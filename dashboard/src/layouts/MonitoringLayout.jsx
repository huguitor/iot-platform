import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { fetchDevices, fetchTelemetry, sendCommand } from '../api';
import DeviceList from '../components/DeviceList';
import DeviceDetail from '../components/DeviceDetail';
import MqttEventsPanel from '../components/MqttEventsPanel';

export default function MonitoringLayout({ token, role, showPlatformHome }) {
  const location = useLocation();
  const [devices, setDevices] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const isAdminLike = role === 'admin' || role === 'superadmin';

  const onlineDevices = useMemo(
    () => devices.filter((device) => (device.status || '').toLowerCase() === 'online'),
    [devices]
  );

  const selected = useMemo(
    () => onlineDevices.find((device) => device.device_id === selectedId) || null,
    [onlineDevices, selectedId]
  );

  useEffect(() => {
    let active = true;

    const refreshDevices = async () => {
      try {
        const list = await fetchDevices(token);
        if (!active) return;

        setDevices(list);
        const onlyOnline = list.filter(
          (device) => (device.status || '').toLowerCase() === 'online'
        );
        setSelectedId((previous) => {
          if (!onlyOnline.length) return null;
          if (previous && onlyOnline.some((device) => device.device_id === previous)) {
            return previous;
          }
          return onlyOnline[0].device_id;
        });
      } catch (error) {
        console.error('Error refreshing devices:', error);
      }
    };

    refreshDevices();
    const intervalId = window.setInterval(refreshDevices, 10000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [token]);

  useEffect(() => {
    let active = true;

    if (!selectedId) {
      setTelemetry([]);
      return undefined;
    }

    const refreshTelemetry = async () => {
      try {
        const data = await fetchTelemetry(selectedId, token);
        if (active) setTelemetry(data);
      } catch (error) {
        console.error('Error refreshing telemetry:', error);
      }
    };

    refreshTelemetry();
    const intervalId = window.setInterval(refreshTelemetry, 8000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [selectedId, token]);

  const handleSendCommand = (command) => {
    if (!selectedId) return undefined;
    return sendCommand(selectedId, command, token);
  };

  const isActive = (path) => location.pathname === path;

  return (
    <>
      <div className="area-heading">
        <div>
          <p className="eyebrow">Área de trabajo</p>
          <h2>Monitoreo IoT</h2>
        </div>
        {showPlatformHome && (
          <Link className="ghost area-back-link" to="/">Volver a IoT Platform</Link>
        )}
      </div>

      <nav className="nav-tabs" aria-label="Navegación de Monitoreo y Telemetría">
        <Link className={`tab ${isActive('/monitoreo') ? 'active' : ''}`} to="/monitoreo">
          Dashboard
        </Link>
        {isAdminLike && (
          <Link className={`tab ${isActive('/monitoreo/mqtt') ? 'active' : ''}`} to="/monitoreo/mqtt">
            Histórico MQTT
          </Link>
        )}
      </nav>

      <Routes>
        <Route
          index
          element={(
            <section className="content">
              <DeviceList
                devices={onlineDevices}
                onSelect={setSelectedId}
                selectedId={selectedId}
              />
              <DeviceDetail
                device={selected}
                telemetry={telemetry}
                onSendCommand={handleSendCommand}
                token={token}
                role={role}
              />
            </section>
          )}
        />
        <Route path="mqtt" element={isAdminLike ? <MqttEventsPanel token={token} /> : <Navigate to="/monitoreo" replace />} />
        <Route path="*" element={<Navigate to="/monitoreo" replace />} />
      </Routes>
    </>
  );
}
