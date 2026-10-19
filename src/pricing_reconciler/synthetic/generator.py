
from __future__ import annotations

import random
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

import pandas as pd
from sqlalchemy import Engine, insert
from sqlalchemy.exc import SAWarning

from .schema import create_schema, ecc_metadata, s4_metadata

END_OF_TIME = date(9999, 12, 31)
CURRENCY = {"1000": "EUR", "2000": "GBP"}
BASE_TAX = {"1000": Decimal("21.0"), "2000": Decimal("20.0")}
CONDITION_COLUMNS = [
    "kschl",
    "vkorg",
    "kunnr",
    "matnr",
    "datab",
    "datbi",
    "kbetr",
    "konwa",
    "kpein",
    "kmein",
    "scale_qty",
    "changed_on",
]


def ecc_customer(cid: int) -> str:
    return f"{cid:010d}"


def s4_customer(cid: int) -> str:
    return f"BP{cid:08d}"


def ecc_material(mid: int) -> str:
    return f"M{mid:08d}"


def s4_material(mid: int) -> str:
    return f"MAT-{mid:010d}"


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class SyntheticDataset:
    # everything the seeder writes, plus the answer key

    as_of: date
    cutoff: datetime
    ecc_conditions: pd.DataFrame
    ecc_scope: pd.DataFrame
    s4_conditions: pd.DataFrame
    map_customer: pd.DataFrame
    map_material: pd.DataFrame
    planted: pd.DataFrame


def generate(
    as_of: date,
    *,
    n_customers: int = 300,
    n_materials: int = 200,
    n_scope: int = 3000,
    rows_per_scenario: int = 6,
    seed: int = 42,
    plant: bool = True,
) -> SyntheticDataset:
    if n_customers < 120 or n_materials < 20:
        raise ValueError("Need at least 120 customers and 20 materials to plant every scenario.")

    rng = random.Random(seed)
    cutoff = datetime.combine(as_of, time(23, 59, 59))
    sales_orgs = list(CURRENCY)

    customers = list(range(1, n_customers + 1))
    materials = list(range(1, n_materials + 1))
    shuffled = customers[:]
    rng.shuffle(shuffled)
    unmapped_customers = set(shuffled[:2])
    taxfree = set(shuffled[2:14])  # no discount / rebate, 0% tax: used for the rounding case
    clean = set(shuffled[14:74])  # no customer-level discount / rebate: used for record cases
    unmapped_materials = set(rng.sample(materials, 2))
    mapped_materials = [m for m in materials if m not in unmapped_materials]
    customer_org = {c: rng.choice(sales_orgs) for c in customers}

    def default_valid_from() -> date:
        return as_of - timedelta(days=rng.randint(60, 400))

    def default_changed_on() -> datetime:
        return cutoff - timedelta(days=rng.randint(2, 300), hours=rng.randint(1, 12))

    def make(
        kschl: str,
        vkorg: str,
        kunnr: str,
        matnr: str,
        kbetr: Decimal,
        konwa: str,
        *,
        kpein: int = 1,
        kmein: str = "",
        scale_qty: int = 0,
        datab: date | None = None,
        datbi: date = END_OF_TIME,
        changed_on: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "kschl": kschl,
            "vkorg": vkorg,
            "kunnr": kunnr,
            "matnr": matnr,
            "datab": datab or default_valid_from(),
            "datbi": datbi,
            "kbetr": kbetr,
            "konwa": konwa,
            "kpein": kpein,
            "kmein": kmein,
            "scale_qty": scale_qty,
            "changed_on": changed_on or default_changed_on(),
        }

    # 1. ECC baseline
    ecc: list[dict[str, Any]] = []
    material_price: dict[tuple[str, int], Decimal] = {}
    for vkorg in sales_orgs:
        cur = CURRENCY[vkorg]
        ecc.append(make("MWST", vkorg, "", "", BASE_TAX[vkorg], "%"))
        for mid in materials:
            price = _money(rng.uniform(5, 500))
            material_price[(vkorg, mid)] = price
            ecc.append(make("PR00", vkorg, "", ecc_material(mid), price, cur, kmein="EA"))
            if rng.random() < 0.30:  # a second scale tier from quantity 100
                ecc.append(
                    make(
                        "PR00",
                        vkorg,
                        "",
                        ecc_material(mid),
                        _money(price * Decimal("0.9")),
                        cur,
                        kmein="EA",
                        scale_qty=100,
                    )
                )
            if rng.random() < 0.70:
                ecc.append(
                    make(
                        "KF00",
                        vkorg,
                        "",
                        ecc_material(mid),
                        _money(rng.uniform(0.5, 2.5)),
                        cur,
                        kmein="EA",
                    )
                )

    for cid in customers:
        vkorg, key = customer_org[cid], ecc_customer(cid)
        plain = cid not in clean and cid not in taxfree
        if plain and rng.random() < 0.60:
            ecc.append(
                make("K007", vkorg, key, "", Decimal(str(rng.choice([2.5, 5.0, 7.5, 10.0]))), "%")
            )
        if plain and rng.random() < 0.30:
            ecc.append(make("ZREB", vkorg, key, "", Decimal(str(rng.choice([1.0, 2.0, 3.0]))), "%"))
        if cid in taxfree or (plain and rng.random() < 0.10):
            ecc.append(make("MWST", vkorg, key, "", Decimal("0.0"), "%"))

    # 2. pricing scope
    scope: set[tuple[str, int, int]] = set()

    def add_scope(cid: int, mid: int) -> tuple[str, int, int]:
        row = (customer_org[cid], cid, mid)
        scope.add(row)
        return row

    clean_rows = [add_scope(c, m) for c in sorted(clean) for m in rng.sample(mapped_materials, 8)]
    taxfree_rows = [
        add_scope(c, m) for c in sorted(taxfree) for m in rng.sample(mapped_materials, 6)
    ]
    regular_customers = [c for c in customers if c not in unmapped_customers]
    pooled = clean | taxfree
    ordinary = [c for c in regular_customers if c not in pooled]

    unmapped_rows: list[tuple[tuple[str, int, int], str]] = []
    if plant:
        for cid in sorted(unmapped_customers):
            for mid in rng.sample(mapped_materials, 3):
                unmapped_rows.append(
                    (add_scope(cid, mid), f"customer {ecc_customer(cid)} not mapped")
                )
        for mid in sorted(unmapped_materials):
            for cid in rng.sample(ordinary, 3):
                unmapped_rows.append(
                    (add_scope(cid, mid), f"material {ecc_material(mid)} not mapped")
                )

    attempts = 0
    while len(scope) < n_scope and attempts < n_scope * 20:
        attempts += 1
        add_scope(rng.choice(ordinary), rng.choice(mapped_materials))

    # 3. S/4 baseline
    map_c = {ecc_customer(c): s4_customer(c) for c in customers if c not in unmapped_customers}
    map_m = {ecc_material(m): s4_material(m) for m in mapped_materials}

    def translate(record: Mapping[str, Any]) -> dict[str, Any] | None:
        kunnr, matnr = record["kunnr"], record["matnr"]
        if kunnr and kunnr not in map_c:
            return None  # customer was never migrated, so its records are not in S/4
        if matnr and matnr not in map_m:
            return None
        out = dict(record)
        out["kunnr"] = map_c[kunnr] if kunnr else ""
        out["matnr"] = map_m[matnr] if matnr else ""
        return out

    s4 = [t for t in (translate(r) for r in ecc) if t is not None]

    # 4. planted defects
    planted: list[dict[str, Any]] = []
    pool = {"clean": clean_rows[:], "taxfree": taxfree_rows[:]}
    rng.shuffle(pool["clean"])
    rng.shuffle(pool["taxfree"])

    def take(which: str) -> list[tuple[str, int, int]]:
        if not plant:
            return []
        return [pool[which].pop() for _ in range(rows_per_scenario)]

    def note(
        scenario: str,
        rule: str,
        status: str,
        row: tuple[str, int, int],
        detail: str,
        flagged: bool = True,
    ) -> None:
        vkorg, cid, mid = row
        planted.append(
            {
                "scenario": scenario,
                "expected_rule": rule,
                "expected_status": status,
                "expect_flagged": flagged,
                "vkorg": vkorg,
                "kunnr": ecc_customer(cid),
                "matnr": ecc_material(mid),
                "detail": detail,
            }
        )

    def specific(
        kschl: str, row: tuple[str, int, int], system: str, kbetr: Decimal, konwa: str, **extra: Any
    ) -> None:
        vkorg, cid, mid = row
        if system == "ecc":
            ecc.append(
                make(kschl, vkorg, ecc_customer(cid), ecc_material(mid), kbetr, konwa, **extra)
            )
        else:
            s4.append(make(kschl, vkorg, s4_customer(cid), s4_material(mid), kbetr, konwa, **extra))

    def both(
        kschl: str, row: tuple[str, int, int], kbetr: Decimal, konwa: str, **extra: Any
    ) -> None:
        specific(kschl, row, "ecc", kbetr, konwa, **extra)
        specific(kschl, row, "s4", kbetr, konwa, **extra)

    def price() -> Decimal:
        return _money(rng.uniform(40, 300))

    # P1: a customer/material discount exists in ECC and was never loaded to S/4.
    for row in take("clean"):
        specific("K007", row, "ecc", Decimal("12.0"), "%")
        note("missing_record", "R04", "MISMATCH", row, "ECC discount record absent in S/4")

    # P2: record migrated, but its valid-to date is already in the past in S/4.
    for row in take("clean"):
        specific("K007", row, "ecc", Decimal("8.0"), "%", datab=as_of - timedelta(days=200))
        specific(
            "K007",
            row,
            "s4",
            Decimal("8.0"),
            "%",
            datab=as_of - timedelta(days=200),
            datbi=as_of - timedelta(days=10),
        )
        note("validity_gap", "R07", "MISMATCH", row, "S/4 record expired 10 days before the cut")

    # P3: only the first scale tier was migrated.
    for row in take("clean"):
        base = price()
        specific("PR00", row, "ecc", base, CURRENCY[row[0]], kmein="EA")
        specific(
            "PR00",
            row,
            "ecc",
            _money(base * Decimal("0.85")),
            CURRENCY[row[0]],
            kmein="EA",
            scale_qty=100,
        )
        specific("PR00", row, "s4", base, CURRENCY[row[0]], kmein="EA")
        note("scale_lost", "R08", "MISMATCH", row, "S/4 has only the first scale tier")

    # P4: same record, value altered during the load.
    for row in take("clean"):
        base = price()
        specific("PR00", row, "ecc", base, CURRENCY[row[0]], kmein="EA")
        specific("PR00", row, "s4", _money(base * Decimal("1.04")), CURRENCY[row[0]], kmein="EA")
        note("amount_changed", "R09", "MISMATCH", row, "S/4 price is 4% higher")

    # P5a: pricing unit lost (per 100 became per 1).  P5b: unit of measure changed.
    for index, row in enumerate(take("clean")):
        base = _money(rng.uniform(100, 400))
        cur = CURRENCY[row[0]]
        if index % 2 == 0:
            specific("PR00", row, "ecc", base, cur, kpein=100, kmein="EA")
            specific("PR00", row, "s4", base, cur, kpein=1, kmein="EA")
            note("pricing_unit", "R03", "MISMATCH", row, "price per 100 became price per 1")
        else:
            specific("PR00", row, "ecc", base, cur, kmein="EA")
            specific("PR00", row, "s4", base, cur, kmein="CS")
            note("unit_of_measure", "R03", "MISMATCH", row, "unit EA became CS, same number")

    # P6: same amount, different currency.
    for row in take("clean"):
        base = price()
        specific("PR00", row, "ecc", base, CURRENCY[row[0]], kmein="EA")
        specific("PR00", row, "s4", base, "USD", kmein="EA")
        note("currency", "R02", "MISMATCH", row, f"{CURRENCY[row[0]]} became USD")

    # P8: a customer/material price exists in ECC only, so S/4 falls back to the material price.
    for row in take("clean"):
        baseline = material_price[(row[0], row[2])]
        special = price()
        while special == baseline:
            special = price()
        specific("PR00", row, "ecc", special, CURRENCY[row[0]], kmein="EA")
        note("access_fallback", "R06", "MISMATCH", row, "S/4 used the material-level price")

    # P9: a discount exists in S/4 only.
    for row in take("clean"):
        specific("K007", row, "s4", Decimal("5.0"), "%")
        note("extra_record", "R05", "MISMATCH", row, "S/4 has a discount that ECC does not")

    # P10: one cent of difference on the price (no discount, rebate or tax on these rows).
    for row in take("taxfree"):
        base = Decimal(rng.randint(20, 400)).quantize(Decimal("0.01"))
        specific("PR00", row, "ecc", base, CURRENCY[row[0]], kmein="EA")
        specific("PR00", row, "s4", base + Decimal("0.01"), CURRENCY[row[0]], kmein="EA")
        note("rounding", "R10", "ROUNDING", row, "S/4 price is 0.01 higher")

    # P7: keys with no mapping (rows were chosen when the scope was built).
    for row, detail in unmapped_rows:
        note("no_key_mapping", "R01", "MISMATCH", row, detail)

    # P11: an S/4 record created AFTER the cut-off must be ignored by a run with that cut-off.
    for row in take("clean"):
        specific("K007", row, "s4", Decimal("5.0"), "%", changed_on=cutoff + timedelta(hours=1))
        note(
            "created_after_cutoff",
            "R05",
            "MISMATCH",
            row,
            "only visible to a run with a later cut-off",
            flagged=False,
        )

    # 5. frames
    def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=CONDITION_COLUMNS)

    scope_frame = pd.DataFrame(
        [
            {"vkorg": v, "kunnr": ecc_customer(c), "matnr": ecc_material(m)}
            for v, c, m in sorted(scope)
        ]
    )
    return SyntheticDataset(
        as_of=as_of,
        cutoff=cutoff,
        ecc_conditions=frame(ecc),
        ecc_scope=scope_frame,
        s4_conditions=frame(s4),
        map_customer=pd.DataFrame(
            [{"kunnr_ecc": k, "partner_s4": v} for k, v in sorted(map_c.items())]
        ),
        map_material=pd.DataFrame(
            [{"matnr_ecc": k, "matnr_s4": v} for k, v in sorted(map_m.items())]
        ),
        planted=pd.DataFrame(planted),
    )


def _insert(engine: Engine, table: Any, frame: pd.DataFrame, chunk: int = 2000) -> None:
    records = frame.to_dict("records")
    with engine.begin() as connection:
        for start in range(0, len(records), chunk):
            connection.execute(insert(table), records[start : start + chunk])


def seed_databases(
    dataset: SyntheticDataset, ecc_engine: Engine, s4_engine: Engine, tables: Mapping[str, str]
) -> None:
    ecc_meta = ecc_metadata(tables["ecc_conditions"], tables["ecc_scope"])
    s4_meta = s4_metadata(tables["s4_conditions"], tables["map_customer"], tables["map_material"])
    create_schema(ecc_engine, ecc_meta)
    create_schema(s4_engine, s4_meta)
    with warnings.catch_warnings():
        # SQLite stores decimals as floats; SQL Server keeps them exact. Harmless for the demo.
        warnings.filterwarnings("ignore", category=SAWarning)
        _insert(ecc_engine, ecc_meta.tables[tables["ecc_conditions"]], dataset.ecc_conditions)
        _insert(ecc_engine, ecc_meta.tables[tables["ecc_scope"]], dataset.ecc_scope)
        _insert(s4_engine, s4_meta.tables[tables["s4_conditions"]], dataset.s4_conditions)
        _insert(s4_engine, s4_meta.tables[tables["map_customer"]], dataset.map_customer)
        _insert(s4_engine, s4_meta.tables[tables["map_material"]], dataset.map_material)
