from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import config

# create_engine does not connect: the first query does.
# connect_timeout: a database host that swallows packets would otherwise hold /ready, and every request, for minutes.
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def get_session():
    """One session per request, closed (and so rolled back if uncommitted) afterwards.

    Services commit explicitly before returning. Committing in the exit of a yield
    dependency can run after the response is sent.
    """
    with Session(engine) as session:
        yield session


def begin_snapshot(session):
    """End any open transaction and start a REPEATABLE READ one, so every read in it comes from one snapshot.

    Separate READ COMMITTED reads can straddle a commit and pair a version with another version's rows. The caller
    ends it with session.rollback(). Never use it before a write: REPEATABLE READ turns the guarded UPDATE's
    zero rows into a serialization error, a 500.
    """
    session.rollback()
    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
