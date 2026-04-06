// src/api.js

import axios from 'axios';

const apiBase = import.meta.env.VITE_API_BASE || 'http://localhost/api';

export const api = axios.create({
  baseURL: apiBase,
});

// ============================================================
// LOGIN
// ============================================================
export async function login(username, password) {
  const params = new URLSearchParams();
  params.append('username', username);
  params.append('password', password);

  const res = await api.post('/auth/token', params);

  const token = res.data.access_token;
  let role = 'user';

  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    role = payload.role || 'user';
  } catch (e) {
    // Si falla el decode del JWT, dejamos rol user por defecto
  }

  return { token, role };
}

// ============================================================
// DISPOSITIVOS
// ============================================================
export async function fetchDevices(token) {
  const res = await api.get(
    '/devices',
    token ? { headers: { Authorization: `Bearer ${token}` } } : undefined
  );
  return res.data;
}

export async function fetchTelemetry(deviceId, token, startTs, endTs) {
  const params = {};

  if (startTs) params.start = startTs;
  if (endTs) params.end = endTs;

  const res = await api.get(`/devices/${deviceId}/telemetry`, {
    params,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });

  return res.data;
}

export async function sendCommand(deviceId, command, token) {
  const payload = typeof command === 'string' ? command : JSON.stringify(command);

  const res = await api.post(
    `/devices/${deviceId}/command`,
    { command: payload },
    { headers: { Authorization: `Bearer ${token}` } }
  );

  return res.data;
}

// ============================================================
// ADMIN - USUARIOS
// ============================================================
export async function fetchUsers(token) {
  const res = await api.get('/admin/users', {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

export async function createUser(payload, token) {
  const res = await api.post('/admin/users', payload, {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

export async function upsertPermission(userId, payload, token) {
  const res = await api.put(`/admin/users/${userId}/permissions`, payload, {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

// ============================================================
// ADMIN - AUDITORÍA
// ============================================================
export async function fetchAuditLogs(token, filters = {}) {
  const params = {};

  // límite por defecto
  params.limit = filters.limit || 100;

  // solo agregamos filtros si vienen con valor
  if (filters.user) params.user = filters.user;
  if (filters.device) params.device = filters.device;
  if (filters.action) params.action = filters.action;

  const res = await api.get('/admin/audit', {
    params,
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}
// ============================================================
// ADMIN - AUDITORÍA EVENTOS MQTT
// ============================================================
export async function fetchMqttEvents(token, filters = {}) {
  const params = {};

  if (filters.device_id) params.device_id = filters.device_id;
  if (filters.date_from) params.date_from = filters.date_from;
  if (filters.date_to) params.date_to = filters.date_to;
  if (filters.event_type) params.event_type = filters.event_type;
  params.limit = filters.limit || 200;

  const res = await api.get('/admin/mqtt-events', {
    params,
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}