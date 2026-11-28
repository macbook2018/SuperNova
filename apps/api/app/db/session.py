from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _database_url() -> str:
    url = get_settings().database_url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(_database_url(), pool_pre_ping=True)
# A request can hold a reader connection throughout an external model call.
# Reserve writer capacity so a saturated API pool cannot block its own telemetry.
observation_engine = (
    create_engine(engine.url, pool_pre_ping=True, pool_size=2, max_overflow=0)
    if engine.dialect.name == "postgresql"
    else engine
)


def observation_bind(db: Session) -> Engine | Connection:
    bind = db.get_bind()
    if bind.engine is engine:
        return observation_engine
    # Preserve injected databases and test schemas, including SQLite in-memory connections.
    return bind


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
