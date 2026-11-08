import pytest

from igogs import config


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Les tests n'écrivent jamais dans ~/.local/share/igogs."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
