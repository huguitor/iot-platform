const colors = {
  online: '#34d399',
  offline: '#f87171',
  pending: '#fbbf24',
  unknown: '#9ca3af',
};

export default function StatusBadge({ status = 'unknown' }) {
  const normalized = status?.toLowerCase?.() || 'unknown';
  const color = colors[normalized] || colors.unknown;
  return (
    <span className="status-badge" style={{ backgroundColor: `${color}22`, color }}>
      ● {normalized}
    </span>
  );
}
