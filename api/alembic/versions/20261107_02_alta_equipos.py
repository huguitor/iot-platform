"""Alta automática de equipos (fábrica + reclamo por QR).

Agrega a `devices`:
- `model`: modelo del equipo de fábrica (`ius-device`, `gateway-wiegand`).
- `provisioned_at`: cuándo se fabricó. NULL = equipo viejo, con usuario MQTT a mano.
- `claim_code_hash`: hash del código de reclamo vigente (nunca el código).
- `company_since`: desde cuándo es de su empresa actual. La empresa ve historial
  (telemetría, eventos MQTT) solo desde ese momento. NULL = sin límite (equipos
  que ya tenían empresa antes de esta revisión).
- `mqtt_enabled`: estado del usuario MQTT en Dynamic Security (solo fábrica).

Columnas nullable y sin datos nuevos: la API anterior las ignora.

Revision ID: 20261107_02
Revises: 20261107_01
"""

from alembic import op

revision = "20261107_02"
down_revision = "20261107_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS model VARCHAR(50)")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS provisioned_at TIMESTAMPTZ")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS claim_code_hash VARCHAR(255)")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS company_since TIMESTAMPTZ")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS mqtt_enabled BOOLEAN")


def downgrade():
    raise RuntimeError(
        "Downgrade automático deshabilitado: borraría datos de fábrica. "
        "La API anterior ignora estas columnas; no hace falta volver atrás la base."
    )
