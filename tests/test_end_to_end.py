from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from conftest import AS_OF
from pricing_reconciler.lock import LockBusyError, RunLock
from pricing_reconciler.runner import cleanup_old_outputs, run_once

CUTOFF = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 59, 59)
KEY = ["Sales org", "Customer (ECC)", "Material (ECC)"]


def read_report(path: str | Path) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name="Mismatches", dtype=str, keep_default_na=False)


def key_set(frame: pd.DataFrame, columns: list[str]) -> set[tuple[str, ...]]:
    return set(map(tuple, frame[columns].astype(str).to_numpy()))


@pytest.fixture(scope="module")
def first_run(demo):
    return run_once(demo.config, CUTOFF, ecc_engine=demo.ecc_engine, s4_engine=demo.s4_engine)


def test_every_planted_row_is_found_and_nothing_else(demo, first_run):
    report = read_report(first_run.excel_path)
    expected = demo.dataset.planted[demo.dataset.planted["expect_flagged"]]

    assert key_set(report, KEY) == key_set(expected, ["vkorg", "kunnr", "matnr"])
    assert len(report) == len(expected)


def test_each_planted_row_gets_the_expected_status_and_cause(demo, first_run):
    causes = demo.config.causes
    report = read_report(first_run.excel_path).set_index(KEY)
    expected = demo.dataset.planted[demo.dataset.planted["expect_flagged"]]

    wrong = []
    for item in expected.itertuples():
        found = report.loc[(item.vkorg, item.kunnr, item.matnr)]
        want_code = causes[item.expected_rule]["code"]
        primary = found["All cause codes"].split(", ")[0]
        if found["Status"] != item.expected_status or primary != want_code:
            wrong.append(
                (item.scenario, item.kunnr, item.matnr, found["Status"], primary, want_code)
            )
    assert not wrong, wrong


def test_counts_add_up_and_summary_file_matches(demo, first_run):
    expected_flagged = int(demo.dataset.planted["expect_flagged"].sum())
    assert first_run.rows_compared == len(demo.dataset.ecc_scope)
    assert first_run.rounding + first_run.mismatch == expected_flagged
    assert first_run.perfect == first_run.rows_compared - expected_flagged
    assert first_run.rounding == int((demo.dataset.planted["expected_status"] == "ROUNDING").sum())

    summary = json.loads(Path(first_run.summary_path).read_text(encoding="utf-8"))
    assert summary["mismatch"] == first_run.mismatch
    assert sum(summary["by_cause"].values()) == expected_flagged
    assert "password" not in json.dumps(summary).lower()


def test_report_has_one_sheet_with_the_agreed_columns(first_run):
    frame = read_report(first_run.excel_path)
    for column in [
        "Status",
        "Probable cause",
        "Cause detail",
        "Recommended action",
        "Net value ECC",
        "Net value S/4",
        "Net value Δ",
    ]:
        assert column in frame.columns
    assert set(frame["Status"]) <= {"MISMATCH", "ROUNDING"}
    with pd.ExcelFile(first_run.excel_path) as workbook:
        assert workbook.sheet_names == ["Mismatches"]


def test_record_created_after_the_cutoff_is_ignored_until_the_cutoff_moves(demo, first_run):
    late = demo.dataset.planted[demo.dataset.planted["scenario"] == "created_after_cutoff"]
    assert len(late) > 0
    before = key_set(read_report(first_run.excel_path), KEY)
    assert not (key_set(late, ["vkorg", "kunnr", "matnr"]) & before)

    later = run_once(
        demo.config,
        CUTOFF + timedelta(days=1),
        ecc_engine=demo.ecc_engine,
        s4_engine=demo.s4_engine,
    )
    after = read_report(later.excel_path)
    hits = after.set_index(KEY).loc[list(key_set(late, ["vkorg", "kunnr", "matnr"]))]
    assert set(hits["All cause codes"].str.split(", ").str[0]) == {"EXTRA_RECORD_S4"}


def test_a_rerun_with_the_same_cutoff_gives_the_same_answer(demo, first_run):
    again = run_once(demo.config, CUTOFF, ecc_engine=demo.ecc_engine, s4_engine=demo.s4_engine)
    assert again.excel_path == first_run.excel_path
    assert (again.perfect, again.rounding, again.mismatch) == (
        first_run.perfect,
        first_run.rounding,
        first_run.mismatch,
    )
    assert again.by_cause == first_run.by_cause


def test_a_far_future_pricing_date_is_checked_when_configured(demo, first_run):
    import dataclasses

    settings = dataclasses.replace(demo.config.settings, pricing_date_offsets_days=(0, 30))
    config = dataclasses.replace(demo.config, settings=settings)
    result = run_once(config, CUTOFF, ecc_engine=demo.ecc_engine, s4_engine=demo.s4_engine)
    assert result.rows_compared == 2 * first_run.rows_compared
    assert len(result.pricing_dates) == 2


def test_a_clean_migration_produces_an_empty_report(clean_demo):
    result = run_once(
        clean_demo.config, CUTOFF, ecc_engine=clean_demo.ecc_engine, s4_engine=clean_demo.s4_engine
    )
    assert (result.rounding, result.mismatch) == (0, 0)
    assert result.perfect == result.rows_compared > 0
    frame = pd.read_excel(result.excel_path, sheet_name="Mismatches")
    assert len(frame) == 0 and "Status" in frame.columns  # header only, file still delivered


def test_clean_run_can_skip_the_empty_report(clean_demo, tmp_path):
    import dataclasses

    settings = dataclasses.replace(
        clean_demo.config.settings, write_empty_report=False, output_dir=tmp_path / "out"
    )
    config = dataclasses.replace(clean_demo.config, settings=settings)
    result = run_once(
        config, CUTOFF, ecc_engine=clean_demo.ecc_engine, s4_engine=clean_demo.s4_engine
    )
    assert result.excel_path is None
    assert result.summary_path is not None


def test_a_running_job_blocks_a_second_one(demo, tmp_path):
    import dataclasses

    settings = dataclasses.replace(demo.config.settings, output_dir=tmp_path / "locked")
    config = dataclasses.replace(demo.config, settings=settings)
    with RunLock(settings.output_dir / ".reconciler.lock", stale_hours=6):
        with pytest.raises(LockBusyError):
            run_once(config, CUTOFF, ecc_engine=demo.ecc_engine, s4_engine=demo.s4_engine)
    # lock released afterwards: the next run goes through
    assert (
        run_once(config, CUTOFF, ecc_engine=demo.ecc_engine, s4_engine=demo.s4_engine).rows_compared
        > 0
    )


def test_a_stale_lock_from_a_crashed_run_is_replaced(tmp_path):
    lock_path = tmp_path / ".reconciler.lock"
    lock_path.write_text("pid=1")
    old = time.time() - 10 * 3600
    os.utime(lock_path, (old, old))
    with RunLock(lock_path, stale_hours=6):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_old_outputs_are_cleaned_up(tmp_path):
    names = ["pricing_mismatches_{cutoff:%Y%m%d}.xlsx", "run_summary_{cutoff:%Y%m%d}.json"]
    old_file = tmp_path / "pricing_mismatches_20250101.xlsx"
    new_file = tmp_path / "pricing_mismatches_20261018.xlsx"
    other = tmp_path / "notes.txt"
    for path in (old_file, new_file, other):
        path.write_text("x")
    long_ago = time.time() - 90 * 86400
    os.utime(old_file, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))

    assert cleanup_old_outputs(tmp_path, names, keep_days=30) == 1
    assert not old_file.exists() and new_file.exists() and other.exists()
