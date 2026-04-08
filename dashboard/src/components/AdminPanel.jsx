// src/components/AdminPanel.jsx

import { useEffect, useState } from 'react';
import {
  createUser,
  createUserChannel,
  deleteUserChannel,
  fetchUserChannels,
  fetchUsers,
  getApiErrorMessage,
  updateUserChannel,
  upsertPermission,
} from '../api';

export default function AdminPanel({ token, role }) {
  const [users, setUsers] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    role: 'user',
  });

  const [permForms, setPermForms] = useState({});
  const [channelsByUser, setChannelsByUser] = useState({});
  const [channelForms, setChannelForms] = useState({});

  const canCreateAdmin = role === 'superadmin';

  const loadUsers = async () => {
    if (!token) return;

    try {
      setLoading(true);
      const list = await fetchUsers(token);
      setUsers(list);
      setError('');
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudieron cargar usuarios'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, [token]);

  useEffect(() => {
    if (!canCreateAdmin && newUser.role === 'admin') {
      setNewUser((prev) => ({ ...prev, role: 'user' }));
    }
  }, [canCreateAdmin, newUser.role]);

  const handleCreateUser = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');

    const payload = {
      username: newUser.username.trim(),
      password: newUser.password,
      role: newUser.role,
    };

    try {
      await createUser(payload, token);
      setNewUser({ username: '', password: '', role: 'user' });
      setMessage('Usuario creado');
      await loadUsers();
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo crear usuario'));
    }
  };

  const updatePermForm = (userId, patch) => {
    setPermForms((prev) => ({
      ...prev,
      [userId]: {
        device_id: '',
        can_read: false,
        can_write: false,
        ...prev[userId],
        ...patch,
      },
    }));
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
      await upsertPermission(
        userId,
        {
          ...form,
          device_id: form.device_id.trim(),
        },
        token
      );
      setMessage('Permiso actualizado');
      updatePermForm(userId, {
        device_id: '',
        can_read: false,
        can_write: false,
      });
      await loadUsers();
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo actualizar permiso'));
    }
  };

  const loadChannels = async (userId) => {
    try {
      const list = await fetchUserChannels(userId, token);
      setChannelsByUser((prev) => ({
        ...prev,
        [userId]: list,
      }));
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudieron cargar canales'));
    }
  };

  const updateChannelForm = (userId, patch) => {
    setChannelForms((prev) => ({
      ...prev,
      [userId]: {
        channel_type: 'whatsapp',
        channel_identifier: '',
        ...prev[userId],
        ...patch,
      },
    }));
  };

  const handleCreateChannel = async (userId, e) => {
    e.preventDefault();
    setError('');
    setMessage('');

    const form = channelForms[userId];
    if (!form?.channel_type || !form?.channel_identifier?.trim()) {
      setError('Necesitás indicar tipo e identificador del canal');
      return;
    }

    try {
      await createUserChannel(
        userId,
        {
          channel_type: form.channel_type,
          channel_identifier: form.channel_identifier.trim(),
          is_enabled: true,
          is_verified: false,
        },
        token
      );

      setMessage('Canal agregado');
      updateChannelForm(userId, {
        channel_type: 'whatsapp',
        channel_identifier: '',
      });
      await loadChannels(userId);
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo crear el canal'));
    }
  };

  const handleToggleChannel = async (userId, channel) => {
    setError('');
    setMessage('');

    try {
      await updateUserChannel(
        userId,
        channel.id,
        {
          is_enabled: !channel.is_enabled,
        },
        token
      );

      setMessage(channel.is_enabled ? 'Canal deshabilitado' : 'Canal habilitado');
      await loadChannels(userId);
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo actualizar el canal'));
    }
  };

  const handleDeleteChannel = async (userId, channelId) => {
    setError('');
    setMessage('');

    try {
      await deleteUserChannel(userId, channelId, token);
      setMessage('Canal eliminado');
      await loadChannels(userId);
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo eliminar el canal'));
    }
  };

  return (
    <section className="admin-panel">
      <div className="admin-header">
        <div>
          <p className="eyebrow">Admin</p>
          <h2>Usuarios, permisos y canales</h2>
          <p className="muted">
            Requiere token de admin. Crea usuarios, asigná permisos y vinculá WhatsApp, Telegram u OpenClaw.
          </p>
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
                {canCreateAdmin && <option value="admin">admin</option>}
              </select>
            </label>

            <button className="primary" type="submit" disabled={!token}>
              Crear usuario
            </button>
          </form>
        </div>

        <div className="card">
          <div className="card-head">
            <p className="eyebrow">Usuarios</p>
            <button
              className="ghost"
              type="button"
              onClick={loadUsers}
              disabled={!token || loading}
            >
              Refrescar
            </button>
          </div>

          {loading && <p className="muted">Cargando...</p>}
          {!loading && users.length === 0 && (
            <p className="muted">No hay usuarios o falta token.</p>
          )}

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
                      {p.device_id}: {p.can_read ? 'R' : ''}
                      {p.can_write ? 'W' : ''}
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
                  <button className="secondary" type="submit" disabled={!token}>
                    Guardar
                  </button>
                </form>

                <div className="channel-block">
                  <div className="card-head">
                    <p className="eyebrow">Canales</p>
                    <button
                      className="ghost"
                      type="button"
                      onClick={() => loadChannels(u.id)}
                      disabled={!token}
                    >
                      Cargar canales
                    </button>
                  </div>

                  {(channelsByUser[u.id] || []).length === 0 ? (
                    <p className="muted">Sin canales cargados.</p>
                  ) : (
                    <div className="perm-tags">
                      {(channelsByUser[u.id] || []).map((c) => (
                        <div
                          key={c.id}
                          className="pill"
                          style={{
                            display: 'flex',
                            gap: '0.5rem',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                          }}
                        >
                          <strong>{c.channel_type}</strong>
                          <span>{c.channel_identifier}</span>
                          <span>{c.is_enabled ? 'activo' : 'inactivo'}</span>

                          <button
                            type="button"
                            className="ghost"
                            onClick={() => handleToggleChannel(u.id, c)}
                            disabled={!token}
                          >
                            {c.is_enabled ? 'Deshabilitar' : 'Habilitar'}
                          </button>

                          <button
                            type="button"
                            className="ghost"
                            onClick={() => handleDeleteChannel(u.id, c.id)}
                            disabled={!token}
                          >
                            Eliminar
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  <form
                    className="perm-form"
                    onSubmit={(e) => handleCreateChannel(u.id, e)}
                  >
                    <select
                      className="text-input"
                      value={channelForms[u.id]?.channel_type || 'whatsapp'}
                      onChange={(e) =>
                        updateChannelForm(u.id, { channel_type: e.target.value })
                      }
                      disabled={!token}
                    >
                      <option value="whatsapp">WhatsApp</option>
                      <option value="telegram">Telegram</option>
                      <option value="openclaw">OpenClaw</option>
                      <option value="web">Web</option>
                    </select>

                    <input
                      className="text-input"
                      placeholder="identificador del canal"
                      value={channelForms[u.id]?.channel_identifier || ''}
                      onChange={(e) =>
                        updateChannelForm(u.id, {
                          channel_identifier: e.target.value,
                        })
                      }
                      disabled={!token}
                      required
                    />

                    <button className="secondary" type="submit" disabled={!token}>
                      Agregar canal
                    </button>
                  </form>
                </div>
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