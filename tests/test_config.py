import shutil
from datetime import datetime
from pathlib import Path

import pytest

from pricing_reconciler.config import ConfigError, load_config, resolve_cutoff

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def config_copy(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_repo_configuration_is_valid(base_config):
    assert base_config.waterfall.step_ids == (
        "gross",
        "discount",
        "rebate",
        "freight",
        "net",
        "tax",
        "total",
    )
    assert base_config.waterfall.cond_types == ("PR00", "K007", "ZREB", "KF00", "MWST")
    assert str(base_config.settings.tolerance_rounding) == "0.01"


def test_causes_catalogue_has_every_rule_the_code_can_emit(base_config):
    emitted = {"R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10", "R99"}
    assert emitted <= set(base_config.causes)
    priorities = [int(rule["priority"]) for rule in base_config.causes.values()]
    assert len(priorities) == len(set(priorities)), "priorities must be unique"
    assert (
        base_config.causes["R01"]["priority"]
        < base_config.causes["R10"]["priority"]
        < base_config.causes["R99"]["priority"]
    )


def test_invalid_table_name_is_rejected(tmp_path):
    folder = config_copy(tmp_path)
    edit(
        folder / "settings.yaml",
        "ecc_conditions: ecc_condition_records",
        "ecc_conditions: 'x; DROP TABLE y'",
    )
    with pytest.raises(ConfigError, match="Invalid table name"):
        load_config(folder)


def test_unknown_access_level_is_rejected(tmp_path):
    folder = config_copy(tmp_path)
    edit(
        folder / "waterfall.yaml",
        "PR00: [customer_material, material]",
        "PR00: [customer_material, region]",
    )
    with pytest.raises(ConfigError, match="unknown level 'region'"):
        load_config(folder)


def test_step_referring_to_a_later_step_is_rejected(tmp_path):
    folder = config_copy(tmp_path)
    edit(folder / "waterfall.yaml", "base: [gross], sign: -1 }", "base: [net], sign: -1 }")
    with pytest.raises(ConfigError, match="not defined before it"):
        load_config(folder)


def test_condition_without_access_sequence_is_rejected(tmp_path):
    folder = config_copy(tmp_path)
    edit(folder / "waterfall.yaml", "  ZREB: [customer]\n", "")
    with pytest.raises(ConfigError, match="'ZREB' has no access sequence"):
        load_config(folder)


def test_bad_rounding_mode_is_rejected(tmp_path):
    folder = config_copy(tmp_path)
    edit(folder / "settings.yaml", "ecc: per_step", "ecc: sometimes")
    with pytest.raises(ConfigError, match="Rounding mode"):
        load_config(folder)


def test_missing_file_gives_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path)


def test_missing_environment_variable_gives_a_clear_error(base_config, monkeypatch):
    monkeypatch.delenv("RECON_ECC_URL", raising=False)
    with pytest.raises(ConfigError, match="RECON_ECC_URL"):
        base_config.settings.database_url("ecc")


def test_cutoff_defaults_to_end_of_yesterday():
    now = datetime(2026, 10, 19, 5, 0, 7)
    assert resolve_cutoff(None, "yesterday_end", now) == datetime(2026, 10, 18, 23, 59, 59)
    assert resolve_cutoff(None, "now", now) == datetime(2026, 10, 19, 5, 0, 7)


def test_cutoff_argument_formats():
    assert resolve_cutoff("2026-10-17") == datetime(2026, 10, 17, 23, 59, 59)
    assert resolve_cutoff("2026-10-17T05:00:00") == datetime(2026, 10, 17, 5, 0, 0)
    with pytest.raises(ConfigError, match="Cannot read cutoff"):
        resolve_cutoff("yesterday-ish")
