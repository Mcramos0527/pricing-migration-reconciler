from pathlib import Path

import pytest

from pricing_reconciler.cli import load_env_file, main
from pricing_reconciler.lock import RunLock

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "config")


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ECC_URL", f"sqlite:///{tmp_path / 'demo' / 'ecc.db'}")
    monkeypatch.setenv("RECON_S4_URL", f"sqlite:///{tmp_path / 'demo' / 's4.db'}")
    return tmp_path


def cli(*args):
    return main(["--config-dir", REPO_CONFIG, "--env-file", "no-such-file", *args])


def test_seed_check_run_flow_and_exit_codes(workdir):
    assert cli("seed", "--as-of", "2026-10-18") == 0
    assert (workdir / "demo" / "planted_differences.csv").exists()
    assert cli("check") == 0
    assert cli("run", "--cutoff", "2026-10-18") == 0
    assert cli("run", "--cutoff", "2026-10-18", "--fail-on-mismatch") == 2
    assert (workdir / "output" / "pricing_mismatches_20261018.xlsx").exists()
    assert (workdir / "output" / "run_summary_20261018.json").exists()
    assert (workdir / "logs" / "reconciler.log").exists()


def test_missing_database_variable_returns_1(workdir, monkeypatch):
    monkeypatch.delenv("RECON_ECC_URL")
    assert cli("run", "--cutoff", "2026-10-18") == 1


def test_bad_cutoff_returns_1(workdir):
    cli("seed", "--as-of", "2026-10-18")
    assert cli("run", "--cutoff", "not-a-date") == 1


def test_run_while_another_run_is_active_returns_3(workdir):
    cli("seed", "--as-of", "2026-10-18")
    with RunLock(workdir / "output" / ".reconciler.lock"):
        assert cli("run", "--cutoff", "2026-10-18") == 3


def test_env_file_does_not_override_real_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nRECON_A=from_file\nRECON_B='quoted'\n\nbroken line\n")
    monkeypatch.setenv("RECON_A", "from_environment")
    monkeypatch.delenv("RECON_B", raising=False)
    load_env_file(env_file)
    import os

    assert os.environ["RECON_A"] == "from_environment"
    assert os.environ["RECON_B"] == "quoted"
