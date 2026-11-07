"""Empresas (multiempresa) de la plataforma: equipos, usuarios, auditoría y eventos.

- Crea `companies` y la empresa inicial "Panozo Sistemas" (código `panozo`).
- Agrega `company_id` (nullable) a `devices`, `users`, `audit_logs` y `access_events`.
  - `devices.company_id` NULL = equipo sin empresa (llegó por MQTT); solo lo ve
    el superadmin.
  - `users.company_id` NULL solo para el superadmin.
- Asigna a la empresa inicial todos los equipos y los usuarios que no son
  superadmin; los eventos y la auditoría existentes toman la empresa del equipo o
  del usuario.
- Triggers: `access_events` toma la empresa del equipo y `audit_logs` la del
  usuario o del equipo cuando quien inserta no la indica (el worker MQTT no
  sabe de empresas).

No toca las tablas `access_*` del módulo de control de acceso: ese trabajo pasó
a la app Control de Acceso.

Reemplaza a la revisión `20261105_01` de la rama `wip/multiempresa-acceso`, que
nunca se aplicó (mezclaba esto con la reescritura del control de acceso).

Revision ID: 20261107_01
Revises: 20261104_01
"""

from alembic import op

revision = "20261107_01"
down_revision = "20261104_01"
branch_labels = None
depends_on = None

DEFAULT_COMPANY_NAME = "Panozo Sistemas"
DEFAULT_COMPANY_CODE = "panozo"

PLATFORM_TABLES = ("users", "devices", "audit_logs", "access_events")


def _add_company_column(table: str):
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS company_id INTEGER")
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_{table}_company'
            ) THEN
                ALTER TABLE {table}
                ADD CONSTRAINT fk_{table}_company
                FOREIGN KEY (company_id) REFERENCES companies(id);
            END IF;
        END $$;
    """)
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_company_id ON {table} (company_id)")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("La migración de empresas requiere PostgreSQL")

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")

    # ------------------------------------------------------------
    # Empresas
    # ------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            code VARCHAR(50) NOT NULL,
            tax_id VARCHAR(30),
            contact_email VARCHAR(150),
            contact_phone VARCHAR(50),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_companies_name UNIQUE (name),
            CONSTRAINT uq_companies_code UNIQUE (code)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_id ON companies (id)")
    op.execute(f"""
        INSERT INTO companies (name, code)
        VALUES ('{DEFAULT_COMPANY_NAME}', '{DEFAULT_COMPANY_CODE}')
        ON CONFLICT (code) DO NOTHING
    """)
    default_id = f"(SELECT id FROM companies WHERE code = '{DEFAULT_COMPANY_CODE}')"

    # ------------------------------------------------------------
    # company_id en la plataforma
    # ------------------------------------------------------------
    for table in PLATFORM_TABLES:
        _add_company_column(table)

    op.execute(f"UPDATE users SET company_id = {default_id} WHERE company_id IS NULL AND role <> 'superadmin'")
    op.execute(f"UPDATE devices SET company_id = {default_id} WHERE company_id IS NULL")
    op.execute("""
        UPDATE access_events e SET company_id = d.company_id
        FROM devices d
        WHERE e.company_id IS NULL AND d.device_id = e.device_id
    """)
    op.execute("""
        UPDATE audit_logs a SET company_id = u.company_id
        FROM users u
        WHERE a.company_id IS NULL AND u.id = a.user_id
    """)
    op.execute("""
        UPDATE audit_logs a SET company_id = d.company_id
        FROM devices d
        WHERE a.company_id IS NULL AND a.user_id IS NULL AND d.device_id = a.device_id
    """)

    # ------------------------------------------------------------
    # Triggers: empresa por defecto al insertar
    # ------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION set_access_event_company() RETURNS trigger AS $$
        BEGIN
            IF NEW.company_id IS NULL AND NEW.device_id IS NOT NULL THEN
                SELECT company_id INTO NEW.company_id FROM devices WHERE device_id = NEW.device_id;
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_access_event_company ON access_events")
    op.execute("""
        CREATE TRIGGER trg_access_event_company BEFORE INSERT ON access_events
        FOR EACH ROW EXECUTE FUNCTION set_access_event_company()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION set_audit_log_company() RETURNS trigger AS $$
        BEGIN
            IF NEW.company_id IS NULL AND NEW.user_id IS NOT NULL THEN
                SELECT company_id INTO NEW.company_id FROM users WHERE id = NEW.user_id;
            END IF;
            IF NEW.company_id IS NULL AND NEW.device_id IS NOT NULL THEN
                SELECT company_id INTO NEW.company_id FROM devices WHERE device_id = NEW.device_id;
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_company ON audit_logs")
    op.execute("""
        CREATE TRIGGER trg_audit_log_company BEFORE INSERT ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION set_audit_log_company()
    """)


def downgrade():
    raise RuntimeError(
        "Downgrade automático deshabilitado: borraría la asignación de empresas. "
        "Restaurar desde backup con un procedimiento revisado."
    )
