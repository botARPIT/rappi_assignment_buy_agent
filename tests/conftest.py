import pytest
from fastapi.testclient import TestClient

from backend.server import create_app
from backend.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "purchasing.sqlite3")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3", provider="scripted")) as client:
        yield client
