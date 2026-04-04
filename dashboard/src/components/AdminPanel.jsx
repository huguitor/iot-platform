// src/components/AdminPanel.jsx

import { useEffect, useState } from 'react';
import { createUser, fetchUsers, upsertPermission } from '../api';

export default function AdminPanel({ token }) {
  const [users, setUsers] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' });
  const [permForms, setPermForms] = useState({});

  const loadUsers = async () => {
    if (!token) return;
    try {
      setLoading(true);
      const list = await fetchUsers(token);
      setUsers(list);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudieron cargar usuarios');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, [token]);

  const handleCreateUser = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');
    try {
      await createUser(newUser, token);
      setNewUser({ username: '', password: '', role: 'user' });
      setMessage('Usuario creado');
      loadUsers();
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudo crear usuario');
    }
  };

  const updatePermForm = (userId, patch) => {
    setPermForms((prev) => ({ ...prev, [userId]: { device_id: '', can_read: false, can_write: false, ...prev[userId], ...patch } }));
  };

  const handlePermission = async (userId, e) => {
    e.preventDefault();
    setError('');
    setMessage('');
    const form = permForms[userId];
    if (!form?.device_id) {
      setError('Necesitás indicar device_id');
      return;
    }
    try {
      await upsertPermission(userId, form, token);
      setMessage('Permiso actualizado');
      updatePermForm(userId, { device_id: '', can_read: false, can_write: false });
      loadUsers();
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudo actualizar permiso');
    }
  };

  return (
    <section className="admin-panel">
      <div className="admin-header">
        <div>
          <p className="eyebrow">Admin</p>
          <h2>Usuarios y permisos</h2>
          <p className="muted">Requiere token de admin. Crea usuarios y asigná qué dispositivos pueden ver o comandar.</p>
        </div>
        {!token && <p className="error">Ingresá con el usuario admin para usar este panel.</p>}
      </div>

      <div className="admin-grid">
        <div className="card">
          <p className="eyebrow">Nuevo usuario</p>
          <form className="stack gap" onSubmit={handleCreateUser}>
            <label className="stack sm">
              <span>Username</span>
              <input
                className="text-input"
                value={newUser.username}
                onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                required
                disabled={!token}
              />
            </label>
            <label className="stack sm">
              <span>Password</span>
              <input
                type="password"
                className="text-input"
                value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                required
                disabled={!token}
              />
            </label>
            <label className="stack sm">
              <span>Rol</span>
              <select
                className="text-input"
                value={newUser.role}
                onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                disabled={!token}
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <button className="primary" type="submit" disabled={!token}>Crear usuario</button>
          </form>
        </div>

        <div className="card">
          <div className="card-head">
            <p className="eyebrow">Usuarios</p>
            <button className="ghost" type="button" onClick={loadUsers} disabled={!token || loading}>Refrescar</button>
          </div>
          {loading && <p className="muted">Cargando...</p>}
          {!loading && users.length === 0 && <p className="muted">No hay usuarios o falta token.</p>}
          <div className="user-list">
            {users.map((u) => (
              <div key={u.id} className="user-card">
                <div className="user-row">
                  <div>
                    <p className="eyebrow">{u.role}</p>
                    <h4>{u.username}</h4>
                    <p className="muted">ID {u.id}</p>
                  </div>
                  <div className="tag">{(u.permissions || []).length} permisos</div>
                </div>
                <div className="perm-tags">
                  {(u.permissions || []).map((p) => (
                    <span key={p.id} className="pill">
                      {p.device_id}: {p.can_read ? 'R' : ''}{p.can_write ? 'W' : ''}
                    </span>
                  ))}
                </div>
                <form className="perm-form" onSubmit={(e) => handlePermission(u.id, e)}>
                  <input
                    className="text-input"
                    placeholder="device_id"
                    value={permForms[u.id]?.device_id || ''}
                    onChange={(e) => updatePermForm(u.id, { device_id: e.target.value })}
                    disabled={!token}
                    required
                  />
                  <label className="chk">
                    <input
                      type="checkbox"
                      checked={permForms[u.id]?.can_read || false}
                      onChange={(e) => updatePermForm(u.id, { can_read: e.target.checked })}
                      disabled={!token}
                    />
                    <span>Lectura</span>
                  </label>
                  <label className="chk">
                    <input
                      type="checkbox"
                      checked={permForms[u.id]?.can_write || false}
                      onChange={(e) => updatePermForm(u.id, { can_write: e.target.checked })}
                      disabled={!token}
                    />
                    <span>Comando</span>
                  </label>
                  <button className="secondary" type="submit" disabled={!token}>Guardar</button>
                </form>
              </div>
            ))}
          </div>
        </div>
      </div>

      {(error || message) && (
        <div className="feedback">
          {error && <p className="error">{error}</p>}
          {message && !error && <p className="success">{message}</p>}
        </div>
      )}
    </section>
  );
}
