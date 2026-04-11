import asyncio
import importlib
import sys
from unittest.mock import patch


class _FakeSession:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _import_database_module():
    sys.modules.pop("app.database", None)
    with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=object()), patch(
        "sqlalchemy.ext.asyncio.async_sessionmaker", return_value=lambda: None
    ):
        return importlib.import_module("app.database")


def test_get_db_commits_on_success(monkeypatch):
    database = _import_database_module()
    fake_session = _FakeSession()
    monkeypatch.setattr(database, "async_session_factory", lambda: _SessionContext(fake_session))

    async def _run():
        gen = database.get_db()
        session = await anext(gen)
        assert session is fake_session
        try:
            await anext(gen)
        except StopAsyncIteration:
            pass

    asyncio.run(_run())

    assert fake_session.committed == 1
    assert fake_session.rolled_back == 0


def test_get_db_rolls_back_on_exception(monkeypatch):
    database = _import_database_module()
    fake_session = _FakeSession()
    monkeypatch.setattr(database, "async_session_factory", lambda: _SessionContext(fake_session))

    async def _run():
        gen = database.get_db()
        await anext(gen)
        try:
            await gen.athrow(RuntimeError("boom"))
        except RuntimeError:
            pass

    asyncio.run(_run())

    assert fake_session.committed == 0
    assert fake_session.rolled_back == 1
