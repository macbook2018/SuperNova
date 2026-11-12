from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.models import (  # noqa: F401
    ApiKey,
    KnowledgeBase,
    ModelProvider,
    Persona,
    PersonaKnowledgeBase,
    RefreshSession,
    Tenant,
    TenantMembership,
    User,
)
from sqlalchemy import create_engine, pool

target_metadata = Base.metadata


def _database_url() -> str:
    url = get_settings().database_url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
