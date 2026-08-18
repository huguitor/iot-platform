// src/pages/AccessControlPage.jsx
import { Link } from 'react-router-dom';
import AccessControlPanel from '../components/AccessControlPanel';

export default function AccessControlPage({ token, showPlatformHome }) {
    return (
        <div className="access-fullscreen area-page">
            <div className="area-heading">
                <div>
                    <p className="eyebrow">Área de trabajo</p>
                    <h2>Control de Acceso</h2>
                </div>
                {showPlatformHome && (
                    <Link className="ghost area-back-link" to="/">
                        Volver a IoT Platform
                    </Link>
                )}
            </div>
            <nav className="nav-tabs" aria-label="Navegación de Control de Acceso">
                <Link className="tab active" to="/acceso">Dashboard / Eventos</Link>
            </nav>
            <AccessControlPanel token={token} />
        </div>
    );
}
