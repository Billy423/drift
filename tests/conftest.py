import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from drift.persistence.db import DATABASE_URL, Base


@pytest.fixture(scope="session")
def _engine():
    engine = create_engine(DATABASE_URL, future=True)
    Base.metadata.create_all(engine)  # tests use metadata; prod uses Alembic
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_engine):
    """Transactional fixture: each test runs in a transaction rolled back at teardown."""
    connection = _engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
