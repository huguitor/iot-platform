// Formato de fecha/hora único del dashboard: 24 horas, zona horaria configurada.
const configuredZone = import.meta.env.VITE_TIME_ZONE;
export const TIME_ZONE = (() => {
  try {
    if (configuredZone) new Intl.DateTimeFormat('es-AR', { timeZone: configuredZone });
    return configuredZone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return 'UTC';
  }
})();

export function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('es-AR', { hourCycle: 'h23', timeZone: TIME_ZONE });
}

export function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString('es-AR', { hourCycle: 'h23', timeZone: TIME_ZONE });
}
