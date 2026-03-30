import { useEffect, useMemo, useState } from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from 'recharts';

const timeZone = import.meta.env.VITE_TIME_ZONE || Intl.DateTimeFormat().resolvedOptions().timeZone;

function coerceNumbers(obj) {
  if (!obj) return {};
  const out = {};
  Object.entries(obj).forEach(([k, v]) => {
    // Try to coerce numeric-looking strings so Recharts treats them as numbers.
    const num = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
    out[k] = Number.isFinite(num) ? num : v;
  });
  return out;
}

export default function TelemetryChart({ telemetry }) {
  const [metric, setMetric] = useState('');
  const [windowHours, setWindowHours] = useState(2);

  const data = useMemo(
    () =>
      [...(telemetry || [])]
        .map((t) => {
          const date = new Date(t.timestamp);
          return {
            ts: date.getTime(),
            timeLabel: date.toLocaleTimeString('es-AR', { hour12: false, timeZone }),
            ...coerceNumbers(t.payload),
          };
        })
        .sort((a, b) => a.ts - b.ts), // ensure chronological order for clean lines
    [telemetry]
  );

  const windowedData = useMemo(() => {
    if (!data.length) return [];
    const latestTs = data[data.length - 1].ts || Date.now();
    const cutoff = latestTs - windowHours * 3600 * 1000;
    return data.filter((d) => d.ts >= cutoff);
  }, [data, windowHours]);

  // Take metrics from the most recent payload in the current window so the dropdown
  // always matches the fields that device is sending now (evita mostrar métricas viejas
  // como humedad/temperatura cuando ya no vienen en el JSON).
  const latestRow = useMemo(
    () => windowedData[windowedData.length - 1] || data[data.length - 1],
    [data, windowedData]
  );

  const numericKeys = useMemo(() => {
    if (!latestRow) return [];
    return Object.entries(latestRow)
      .filter(([k, v]) => !['ts', 'timeLabel'].includes(k) && Number.isFinite(v))
      .map(([k]) => k);
  }, [latestRow]);

  // Default to first numeric metric when data arrives.
  useEffect(() => {
    if ((!metric || !numericKeys.includes(metric)) && numericKeys.length > 0) {
      setMetric(numericKeys[0]);
    }
    if (metric && numericKeys.length === 0) {
      setMetric('');
    }
  }, [metric, numericKeys]);

  const xDomain = useMemo(() => {
    if (!windowedData.length && !data.length) return ['auto', 'auto'];
    const latestTs = (windowedData[windowedData.length - 1] || data[data.length - 1]).ts || Date.now();
    const cutoff = latestTs - windowHours * 3600 * 1000;
    return [cutoff, latestTs];
  }, [data, windowHours, windowedData]);

  const yDomain = useMemo(() => {
    if (!metric || !windowedData.length) return [0, 'auto'];
    const values = windowedData.map((d) => d[metric]).filter((v) => Number.isFinite(v));
    if (!values.length) return [0, 'auto'];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(1, max - min || 1);
    const pad = span * 0.1;
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [metric, windowedData]);

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Telemetría</p>
          <h3>Sensores en vivo</h3>
        </div>
      </div>
      <div style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={windowedData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="ts"
              type="number"
              tickFormatter={(value) => new Date(value).toLocaleTimeString('es-AR', { hour12: false, timeZone })}
              stroke="#9ca3af"
              tick={{ fontSize: 12 }}
              domain={xDomain}
              allowDataOverflow
            />
            <YAxis stroke="#9ca3af" tick={{ fontSize: 12 }} domain={yDomain} allowDataOverflow />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #1f2937' }}
              labelFormatter={(value) => new Date(value).toLocaleTimeString('es-AR', { hour12: false, timeZone })}
              formatter={(value, name) => [value, name]}
            />
            {metric && (
              <Line
                type="monotone"
                dataKey={metric}
                stroke={chartColors[0]}
                dot={{ r: 2 }}
                connectNulls
                isAnimationActive={false}
                strokeWidth={2}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!windowedData.length && <p className="muted">Sin datos en la ventana seleccionada.</p>}
      <div className="chart-controls">
        <div className="control">
          <label>Métrica</label>
          <select value={metric} onChange={(e) => setMetric(e.target.value)} className="text-input">
            {numericKeys.map((key) => (
              <option key={key} value={key}>{key}</option>
            ))}
          </select>
        </div>
        <div className="control">
          <label>Ventana (horas)</label>
          <input
            type="range"
            min="1"
            max="24"
            step="1"
            value={windowHours}
            onChange={(e) => setWindowHours(Number(e.target.value))}
          />
          <span className="muted">{windowHours}h hacia atrás</span>
        </div>
      </div>
    </div>
  );
}

const chartColors = ['#0ea5e9', '#f59e0b', '#10b981', '#e11d48', '#c084fc'];
