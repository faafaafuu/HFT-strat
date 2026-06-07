from pathlib import Path

from tools.verify_persistence import _seed, _verify


def test_paper_state_survives_database_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.sqlite3"
    url = f"sqlite:///{db_path}"

    _seed(url)
    _verify(url)
