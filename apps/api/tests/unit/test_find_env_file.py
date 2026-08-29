"""`_find_env_file` returns the outermost `.env` above `app/core/config.py`.

The repo-root `.env` is canonical (owner, 2026-08-29). For two days a copy under
`apps/api/` shadowed it with a stale `ADMIN_KEY`, because the walk stopped at the
first file it met. Each test builds its own tree under `tmp_path` and bounds the
walk to it, so no real env file is read and no value is ever printed.
"""

from pathlib import Path

from app.core.config import _find_env_file


def _config_file(root: Path) -> Path:
    """The path `app/core/config.py` would have inside a checkout at `root`."""
    config = root / "apps" / "api" / "app" / "core" / "config.py"
    config.parent.mkdir(parents=True)
    config.write_text("")
    return config


def test_the_repo_root_env_wins_over_a_copy_under_apps_api(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    (tmp_path / ".env").write_text("ADMIN_KEY=root\n")
    (tmp_path / "apps" / "api" / ".env").write_text("ADMIN_KEY=stale\n")

    assert _find_env_file(config, stop=tmp_path) == str(tmp_path / ".env")


def test_a_single_env_file_anywhere_above_is_found(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    only = tmp_path / "apps" / "api" / ".env"
    only.write_text("X=1\n")

    assert _find_env_file(config, stop=tmp_path) == str(only)


def test_no_env_file_is_none(tmp_path: Path) -> None:
    assert _find_env_file(_config_file(tmp_path), stop=tmp_path) is None
