import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.models import Base

# Reads the variable directly, not app.config: migrating needs no tokens.
# ponytail: online mode only. Offline SQL scripts are not needed while the deploy runs `upgrade head` itself.
engine = create_engine(os.environ["APP_DATABASE_URL"], poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()
