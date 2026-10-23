from __future__ import annotations

import logging

import pytest

from sourcereport.config import Settings
from sourcereport.models import Metrics
from sourcereport.observability import configure_logging
from sourcereport.storage import SQLiteStore


@pytest.fixture
def settings_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    monkeypatch.delenv("SOURCEREPORT_DB", raising=False)
    return tmp_path


def test_fresh_install_uses_new_database_name(settings_environment):
    assert Settings.from_env().database_path == "data/sourcereport.db"
    assert Settings().database_path == "data/sourcereport.db"


def test_configured_database_path(monkeypatch, settings_environment):
    monkeypatch.setenv("SOURCEREPORT_DB", "data/current.db")
    assert Settings.from_env().database_path == "data/current.db"


def test_saved_history_is_accessible_from_configured_database(monkeypatch, settings_environment):
    store = SQLiteStore("data/history.db")
    state = {
        "task_id": "saved-task", "question": "A saved research question?",
        "report": "An existing report", "metrics": Metrics(started_at=0).model_dump(),
    }
    store.save_state(state, "completed")
    monkeypatch.setenv("SOURCEREPORT_DB", "data/history.db")
    settings = Settings.from_env()
    assert settings.database_path == "data/history.db"
    assert SQLiteStore(settings.database_path).load_state("saved-task") == state
    assert not (settings_environment / "data/sourcereport.db").exists()


def test_empty_database_setting_uses_default(monkeypatch, settings_environment):
    monkeypatch.setenv("SOURCEREPORT_DB", "")
    assert Settings.from_env().database_path == "data/sourcereport.db"


@pytest.mark.parametrize("configured,explicit,expected", [
    (None, None, logging.INFO),
    ("WARNING", None, logging.WARNING),
    ("WARNING", "DEBUG", logging.DEBUG),
])
def test_log_level_configuration(monkeypatch, configured, explicit, expected):
    monkeypatch.delenv("SOURCEREPORT_LOG_LEVEL", raising=False)
    if configured:
        monkeypatch.setenv("SOURCEREPORT_LOG_LEVEL", configured)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(explicit)
        assert root.level == expected
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
