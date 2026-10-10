from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, email="operator@example.test", password="test-password"
    )


@pytest.fixture
def legacy_html():
    return (Path(__file__).parent / "fixtures" / "legacy.html").read_text()


@pytest.fixture
def v2_html():
    return (Path(__file__).parent / "fixtures" / "v2.html").read_text()
