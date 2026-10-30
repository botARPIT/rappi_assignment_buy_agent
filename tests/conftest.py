import pytest

from backend.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "purchasing.sqlite3")
