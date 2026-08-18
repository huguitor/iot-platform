// src/api.js

import axios from 'axios';

const apiBase = import.meta.env.VITE_API_BASE || 'http://localhost/api';

export const api = axios.create({
  baseURL: apiBase,
});

// ============================================================
// HELPER - NORMALIZAR ERRORES BACKEND
// ============================================================
export function getApiErrorMessage(err, fallback = 'Ocurrió un error') {
  const detail = err?.response?.data?.detail;

  if (!detail) return fallback;

  if (typeof detail === 'string') return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item?.msg) {
          const field =
            Array.isArray(item.loc) && item.loc.length
              ? item.loc[item.loc.length - 1]
              : 'campo';
          return `${field}: ${item.msg}`;
        }
        return JSON.stringify(item);
      })
      .join(' | ');
  }

  if (typeof detail === 'object') {
    if (detail.msg) return detail.msg;
    return JSON.stringify(detail);
  }

  return fallback;
}

// ============================================================
// LOGIN
// ============================================================
export async function login(username, password) {
  const params = new URLSearchParams();
  params.append('username', username);
  params.append('password', password);

  const res = await api.post('/auth/token', params);

  return { token: res.data.access_token };
}

export async function fetchCurrentUser(token) {
  const res = await api.get('/auth/me', {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
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

export async function updateModuleAccess(userId, payload, token) {
  const res = await api.put(`/admin/users/${userId}/modules`, payload, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}

// ============================================================
// ADMIN - USER CHANNELS
// ============================================================
export async function fetchUserChannels(userId, token) {
  const res = await api.get(`/admin/users/${userId}/channels`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

export async function createUserChannel(userId, payload, token) {
  const res = await api.post(`/admin/users/${userId}/channels`, payload, {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

export async function updateUserChannel(userId, channelId, payload, token) {
  const res = await api.put(
    `/admin/users/${userId}/channels/${channelId}`,
    payload,
    {
      headers: { Authorization: `Bearer ${token}` },
    }
  );

  return res.data;
}

export async function deleteUserChannel(userId, channelId, token) {
  const res = await api.delete(`/admin/users/${userId}/channels/${channelId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}

// ============================================================
// ADMIN - AUDITORÍA
// ============================================================
export async function fetchAuditLogs(token, filters = {}) {
  const params = {};

  params.limit = filters.limit || 100;

  if (filters.user) params.user = filters.user;
  if (filters.device) params.device = filters.device;
  if (filters.action) params.action = filters.action;
  if (filters.channel_type) params.channel_type = filters.channel_type;
  if (filters.channel_identifier) params.channel_identifier = filters.channel_identifier;

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
  
}// ============================================================
// CONTROL DE ACCESO
// ============================================================
export async function fetchAccessEvents(token, filters = {}) {
  const params = {};

  if (filters.device_id) params.device_id = filters.device_id;
  if (filters.reader) params.reader = filters.reader;
  if (filters.card_uid) params.card_uid = filters.card_uid;
  if (filters.event_type) params.event_type = filters.event_type;
  if (filters.authorized !== undefined) params.authorized = filters.authorized;
  if (filters.date_from) params.date_from = filters.date_from;
  if (filters.date_to) params.date_to = filters.date_to;

  params.limit = filters.limit || 200;

  const res = await api.get('/access/events', {
    params,
    headers: { Authorization: `Bearer ${token}` },
  });

  return res.data;
}
