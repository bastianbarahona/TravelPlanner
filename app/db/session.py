from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import config

# create_engine does not connect: the first query does.
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)


def get_session():
    """One session per request, closed (and so rolled back if uncommitted) afterwards.

    Services commit explicitly before returning. Committing in the exit of a yield
    dependency can run after the response is sent.
    """
    with Session(engine) as session:
        yield session
