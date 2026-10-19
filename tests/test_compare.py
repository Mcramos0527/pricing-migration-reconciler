from decimal import Decimal

import pandas as pd
import pytest

from pricing_reconciler.compare import MISMATCH, PERFECT, ROUNDING, compare_waterfalls

D = Decimal
TOLERANCE = D("0.01")


def flat(row_id, value, *, currency="EUR", uom="EA", steps):
    data = {
        "row_id": row_id,
        "currency": currency if value is not None else "",
        "price_uom": uom if value is not None else "",
    }
    for step in steps:
        data[step] = None if value is None else D(value)
    return data


def grain(*mapped_flags):
    return pd.DataFrame(
        {
            "row_id": range(len(mapped_flags)),
            "pricing_date": 20261018,
            "offset_days": 0,
            "vkorg": "1000",
            "kunnr_ecc": "C",
            "kunnr_s4": ["BP" if m else "" for m in mapped_flags],
            "matnr_ecc": "M",
            "matnr_s4": ["MAT" if m else "" for m in mapped_flags],
            "mapped": list(mapped_flags),
        }
    )


def run(base_config, ecc_rows, s4_rows, mapped_flags):
    steps = base_config.waterfall.step_ids
    columns = ["row_id", "currency", "price_uom", *steps]
    ecc = pd.DataFrame(
        [flat(i, r[0], **r[1], steps=steps) for i, r in enumerate(ecc_rows)], columns=columns
    )
    s4_present = [(i, r) for i, r in enumerate(s4_rows) if r is not None]
    s4 = pd.DataFrame([flat(i, r[0], **r[1], steps=steps) for i, r in s4_present], columns=columns)
    return compare_waterfalls(grain(*mapped_flags), ecc, s4, base_config.waterfall, TOLERANCE)


@pytest.mark.parametrize(
    "ecc_value, s4_value, expected",
    [
        ("100.00", "100.00", PERFECT),
        ("100.00", "100.01", ROUNDING),
        ("100.00", "99.99", ROUNDING),
        ("100.00", "100.02", MISMATCH),
    ],
)
def test_status_follows_the_tolerance(base_config, ecc_value, s4_value, expected):
    result = run(base_config, [(ecc_value, {})], [(s4_value, {})], [True])
    assert result.loc[0, "status"] == expected


def test_different_currency_is_a_mismatch_even_with_equal_numbers(base_config):
    result = run(
        base_config, [("50.00", {"currency": "EUR"})], [("50.00", {"currency": "USD"})], [True]
    )
    assert result.loc[0, "status"] == MISMATCH


def test_different_unit_of_measure_is_a_mismatch_even_with_equal_numbers(base_config):
    result = run(base_config, [("50.00", {"uom": "EA"})], [("50.00", {"uom": "CS"})], [True])
    assert result.loc[0, "status"] == MISMATCH


def test_priced_on_one_side_only_is_a_mismatch(base_config):
    result = run(base_config, [("50.00", {})], [(None, {})], [True])
    assert result.loc[0, "status"] == MISMATCH
    assert result.loc[0, "max_abs_delta"] is None


def test_unpriced_on_both_sides_is_not_a_difference(base_config):
    result = run(base_config, [(None, {})], [(None, {})], [True])
    assert result.loc[0, "status"] == PERFECT


def test_unmapped_row_is_a_mismatch_and_has_no_s4_values(base_config):
    result = run(base_config, [("50.00", {})], [None], [False])
    assert result.loc[0, "status"] == MISMATCH
    assert result.loc[0, "s4_net"] is None


def test_deltas_are_s4_minus_ecc(base_config):
    result = run(base_config, [("100.00", {})], [("100.01", {})], [True])
    assert result.loc[0, "delta_net"] == D("0.01")
    assert result.loc[0, "max_abs_delta"] == D("0.01")
