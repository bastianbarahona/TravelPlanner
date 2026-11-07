import httpx
import pytest
from sqlalchemy import create_engine, text

from app_support import AppProcess, PostgresProcess, app_client as make_app_client, run_alembic
from contract import declared_responses
from support import HTTP_OPTIONS, TOKEN_A, TOKEN_B, Control, MockProcess, exercised, provider_client


@pytest.fixture(scope="session")
def mock(tmp_path_factory):
    process = MockProcess(tmp_path_factory.mktemp("mock") / "mock.db", MOCK_CONTROL_ENABLED="1")
    process.start()
    yield process
    process.kill()


@pytest.fixture
def control(mock):
    """Resets fixtures, clock and faults, so tests do not depend on each other."""
    with httpx.Client(base_url=mock.url, **HTTP_OPTIONS) as http:
        interface = Control(http)
        interface.reset()
        yield interface


@pytest.fixture
def provider(mock, control):
    with provider_client(mock.url, TOKEN_A) as client:
        yield client


@pytest.fixture
def other_caller(mock, control):
    with provider_client(mock.url, TOKEN_B) as client:
        yield client


@pytest.fixture
def unchecked(mock, control):
    """For requests the contract does not declare, such as unknown routes."""
    with httpx.Client(base_url=mock.url, headers={"Authorization": f"Bearer {TOKEN_A}"}, **HTTP_OPTIONS) as client:
        yield client


@pytest.fixture
def start_mock(tmp_path):
    """A private mock instance, for tests that restart it or change its configuration."""
    started = []

    def start(**env):
        process = MockProcess(tmp_path / "private.db", **env)
        process.start()
        started.append(process)
        return process

    yield start
    for process in started:
        process.kill()


@pytest.fixture(scope="session")
def postgres():
    process = PostgresProcess()
    try:
        process.start()
        migration = run_alembic(process.database_url, "upgrade", "head")
        assert migration.returncode == 0, migration.stdout + migration.stderr
        yield process
    finally:
        process.kill()


@pytest.fixture(scope="session")
def db_engine(postgres):
    engine = create_engine(postgres.database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def empty_database(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("TRUNCATE trips, trip_destinations, trip_participants"))
    return db_engine


@pytest.fixture(scope="session")
def application(postgres):
    process = AppProcess(postgres.database_url)
    process.start()
    yield process
    process.kill()


@pytest.fixture
def app_client(application, empty_database):
    with make_app_client(application.url) as client:
        yield client


@pytest.fixture
def start_app(postgres, empty_database):
    started = []

    def start(**env):
        process = AppProcess(postgres.database_url, **env)
        process.start()
        started.append(process)
        return process

    yield start
    for process in started:
        process.kill()


@pytest.fixture
def database_counts(db_engine):
    def counts():
        with db_engine.connect() as connection:
            return tuple(
                connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in ("trips", "trip_destinations", "trip_participants")
            )

    return counts


def pytest_terminal_summary(terminalreporter):
    declared = declared_responses()
    missing = sorted(declared - exercised)
    terminalreporter.write_line(f"contract responses exercised: {len(declared) - len(missing)}/{len(declared)}")
    for operation, status in missing:
        terminalreporter.write_line(f"  not exercised: {operation} {status}")
