export function getHomeMode(access) {
  if (access.accessControl && !access.monitoring) return 'access';
  if (!access.accessControl && access.monitoring) return 'monitoring';
  if (access.accessControl && access.monitoring) return 'selector';
  return 'none';
}

export function resolvePostLoginPath(requestedPath, access) {
  if (requestedPath.startsWith('/acceso')) return '/acceso';
  if (requestedPath.startsWith('/monitoreo')) return '/monitoreo';
  if (requestedPath.startsWith('/sensores')) return '/monitoreo';

  const homeMode = getHomeMode(access);
  if (homeMode === 'access') return '/acceso';
  if (homeMode === 'monitoring') return '/monitoreo';
  return '/';
}

export function canAccessPath(path, access) {
  if (path.startsWith('/acceso')) return access.accessControl;
  if (path.startsWith('/monitoreo') || path.startsWith('/sensores')) {
    return access.monitoring;
  }
  return true;
}
