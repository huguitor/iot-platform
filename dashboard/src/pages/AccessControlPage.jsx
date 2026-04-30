// src/pages/AccessControlPage.jsx
import AccessControlPanel from '../components/AccessControlPanel';

export default function AccessControlPage({ token, username, onBack }) {
    return (
        <div className="access-fullscreen">

            {/* HEADER SIMPLE */}
            <div className="access-header">
                <span>👤 {username}</span>

                <button className="ghost" onClick={onBack}>
                    Volver
                </button>
            </div>

            {/* PANEL */}
            <AccessControlPanel token={token} />
        </div>
    );
}