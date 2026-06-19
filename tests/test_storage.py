"""The persistent SQLite FSM storage — state/data round-trips and survives a 'restart'."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from dbaylo.bot.storage import SQLiteStorage


class _S(StatesGroup):
    waiting = State()


def _key(user_id: int = 7) -> StorageKey:
    return StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)


async def test_state_and_data_round_trip(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "fsm.sqlite")
    key = _key()
    await storage.set_state(key, _S.waiting)
    await storage.set_data(key, {"intake": [{"role": "user", "text": "болить голова"}], "n": 3})

    assert await storage.get_state(key) == _S.waiting.state
    data = await storage.get_data(key)
    assert data["n"] == 3 and data["intake"][0]["text"] == "болить голова"
    await storage.close()


async def test_survives_a_restart(tmp_path) -> None:
    path = tmp_path / "fsm.sqlite"
    key = _key()
    first = SQLiteStorage(path)
    await first.set_state(key, _S.waiting)
    await first.set_data(key, {"report_id": 42})
    await first.close()  # simulate process shutdown

    # A brand-new storage over the same file (a "restarted" process) sees the state.
    second = SQLiteStorage(path)
    assert await second.get_state(key) == _S.waiting.state
    assert (await second.get_data(key))["report_id"] == 42
    await second.close()


async def test_missing_key_defaults(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "fsm.sqlite")
    assert await storage.get_state(_key(999)) is None
    assert await storage.get_data(_key(999)) == {}
    await storage.close()


async def test_clearing_state_keeps_separate_keys(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "fsm.sqlite")
    a, b = _key(1), _key(2)
    await storage.set_state(a, _S.waiting)
    await storage.set_state(b, None)
    assert await storage.get_state(a) == _S.waiting.state
    assert await storage.get_state(b) is None
    await storage.close()
