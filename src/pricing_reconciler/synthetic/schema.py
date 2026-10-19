from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
)


def _condition_table(metadata: MetaData, name: str, key_len: tuple[int, int]) -> Table:
    kunnr_len, matnr_len = key_len
    return Table(
        name,
        metadata,
        Column("record_id", Integer, primary_key=True, autoincrement=True),
        Column("kschl", String(4), nullable=False),  # condition type
        Column("vkorg", String(4), nullable=False),  # sales organisation
        Column(
            "kunnr", String(kunnr_len), nullable=False, default=""
        ),  # customer / business partner
        Column("matnr", String(matnr_len), nullable=False, default=""),  # material number
        Column("datab", Date, nullable=False),  # valid from
        Column("datbi", Date, nullable=False),  # valid to
        Column("kbetr", Numeric(15, 4), nullable=False),  # amount or percentage
        Column("konwa", String(5), nullable=False),  # currency, or '%' for percentages
        Column("kpein", Integer, nullable=False, default=1),  # pricing unit
        Column("kmein", String(3), nullable=False, default=""),  # unit of measure
        Column("scale_qty", Integer, nullable=False, default=0),  # scale lower bound (0 = no scale)
        Column("changed_on", DateTime, nullable=False),  # when the record was created or changed
    )


def ecc_metadata(ecc_conditions: str, ecc_scope: str) -> MetaData:
    metadata = MetaData()
    _condition_table(metadata, ecc_conditions, (10, 18))
    Table(
        ecc_scope,
        metadata,
        Column("scope_id", Integer, primary_key=True, autoincrement=True),
        Column("vkorg", String(4), nullable=False),
        Column("kunnr", String(10), nullable=False),
        Column("matnr", String(18), nullable=False),
    )
    return metadata


def s4_metadata(s4_conditions: str, map_customer: str, map_material: str) -> MetaData:
    metadata = MetaData()
    _condition_table(metadata, s4_conditions, (10, 40))
    Table(
        map_customer,
        metadata,
        Column("kunnr_ecc", String(10), primary_key=True),
        Column("partner_s4", String(10), nullable=False),
    )
    Table(
        map_material,
        metadata,
        Column("matnr_ecc", String(18), primary_key=True),
        Column("matnr_s4", String(40), nullable=False),
    )
    return metadata


def create_schema(engine: Engine, metadata: MetaData, *, drop_first: bool = True) -> None:
    if drop_first:
        metadata.drop_all(engine)
    metadata.create_all(engine)
