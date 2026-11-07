import httpx
import pytest

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


def pytest_terminal_summary(terminalreporter):
    declared = declared_responses()
    missing = sorted(declared - exercised)
    terminalreporter.write_line(f"contract responses exercised: {len(declared) - len(missing)}/{len(declared)}")
    for operation, status in missing:
        terminalreporter.write_line(f"  not exercised: {operation} {status}")
