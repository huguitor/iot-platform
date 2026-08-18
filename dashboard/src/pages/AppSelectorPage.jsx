import { Link } from 'react-router-dom';

export default function AppSelectorPage() {
  return (
    <main className="app-selector">
      <div className="app-selector__heading">
        <p className="eyebrow">IoT Platform</p>
        <h2>Seleccione el área de trabajo</h2>
      </div>

      <div className="app-selector__grid">
        <Link className="app-area-card" to="/acceso">
          <span className="app-area-card__title">Control de Acceso</span>
          <span className="muted">Puertas, lectoras, credenciales y eventos.</span>
        </Link>

        <Link className="app-area-card" to="/monitoreo">
          <span className="app-area-card__title">Monitoreo IoT</span>
          <span className="muted">Dispositivos, estados, telemetría e históricos.</span>
        </Link>
      </div>
    </main>
  );
}
