import axios from 'axios';

const apiBase = import.meta.env.VITE_API_BASE || 'http://localhost/api';

export const api = axios.create({
  baseURL: apiBase,
});

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
    // fallback to user
  }
  return { token, role };
}

export async function fetchDevices(token) {
  const res = await api.get('/devices', token ? { headers: { Authorization: `Bearer ${token}` } } : undefined);
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
    { headers: { Authorization: `Bearer ${token}` } },
  );
  return res.data;
}

export async function fetchUsers(token) {
  const res = await api.get('/admin/users', { headers: { Authorization: `Bearer ${token}` } });
  return res.data;
}

export async function createUser(payload, token) {
  const res = await api.post('/admin/users', payload, { headers: { Authorization: `Bearer ${token}` } });
  return res.data;
}

export async function upsertPermission(userId, payload, token) {
  const res = await api.put(`/admin/users/${userId}/permissions`, payload, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.data;
}
