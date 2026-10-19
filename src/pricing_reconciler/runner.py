
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from .causes import analyse_causes
from .compare import MISMATCH, PERFECT, ROUNDING, compare_waterfalls
from .config import AppConfig, ConfigError
from .db import check_connection, make_engine
from .extract import extract_all
from .lock import RunLock
from .report import build_report_frame, write_excel
from .resolve import build_grain, resolve_conditions
from .waterfall import flatten

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_id: str
    cutoff: str
    pricing_dates: list[str]
    started_at: str
    finished_at: str
    duration_seconds: float
    rows_compared: int
    perfect: int
    rounding: int
    mismatch: int
    by_cause: dict[str, int] = field(default_factory=dict)
    excel_path: str | None = None
    summary_path: str | None = None
    ecc_database: str = ""
    s4_database: str = ""

    @property
    def not_perfect(self) -> int:
        return self.rounding + self.mismatch


def _describe(engine: Engine) -> str:
    return f"{engine.url.host or 'local'}/{engine.url.database}"


def cleanup_old_outputs(directory: Path, name_patterns: list[str], keep_days: int) -> int:
    if keep_days <= 0 or not directory.exists():
        return 0
    limit = time.time() - keep_days * 86400
    removed = 0
    for pattern in name_patterns:
        glob = re.sub(r"\{[^}]*\}", "*", pattern)
        for path in directory.glob(glob):
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
    return removed


def run_once(
    config: AppConfig,
    cutoff: datetime,
    *,
    ecc_engine: Engine | None = None,
    s4_engine: Engine | None = None,
) -> RunResult:
    settings, waterfall = config.settings, config.waterfall
    started = datetime.now()
    clock = time.perf_counter()
    run_id = uuid.uuid4().hex[:8]
    own_engines = ecc_engine is None and s4_engine is None

    with RunLock(settings.output_dir / ".reconciler.lock", settings.lock_stale_hours):
        log.info("Run %s started, cutoff %s", run_id, cutoff.isoformat(timespec="seconds"))
        if own_engines:
            ecc_engine = make_engine(settings.database_url("ecc"))
            s4_engine = make_engine(settings.database_url("s4"))
        assert ecc_engine is not None and s4_engine is not None
        try:
            check_connection(ecc_engine, "ECC")
            check_connection(s4_engine, "S/4HANA")

            data = extract_all(ecc_engine, s4_engine, settings, cutoff)
            if data.scope.empty:
                raise ConfigError("The pricing scope table is empty: nothing to compare.")

            dates = [
                (o, cutoff.date() + timedelta(days=o)) for o in settings.pricing_date_offsets_days
            ]
            grain = build_grain(data.scope, data.map_customer, data.map_material, dates)
            grain_s4 = grain[grain["mapped"]]
            log.info(
                "Grain: %d rows (%d scope rows x %d pricing dates); %d without S/4 mapping",
                len(grain),
                len(data.scope),
                len(dates),
                int((~grain["mapped"]).sum()),
            )

            ecc_resolved = resolve_conditions(
                data.ecc_records, grain, "kunnr_ecc", "matnr_ecc", waterfall
            )
            s4_resolved = resolve_conditions(
                data.s4_records, grain_s4, "kunnr_s4", "matnr_s4", waterfall
            )
            ecc_flat = flatten(
                ecc_resolved, list(grain["row_id"]), waterfall, settings.rounding["ecc"]
            )
            s4_flat = flatten(
                s4_resolved, list(grain_s4["row_id"]), waterfall, settings.rounding["s4"]
            )

            compared = compare_waterfalls(
                grain, ecc_flat, s4_flat, waterfall, settings.tolerance_rounding
            )
            causes = analyse_causes(
                compared,
                grain,
                ecc_resolved,
                s4_resolved,
                data.s4_records,
                waterfall,
                config.causes,
                settings.tolerance_rounding,
            )

            counts = compared["status"].value_counts()
            perfect, rounding, mismatch = (
                int(counts.get(s, 0)) for s in (PERFECT, ROUNDING, MISMATCH)
            )
            by_cause = (
                {str(k): int(v) for k, v in causes["cause_code"].value_counts().items()}
                if not causes.empty
                else {}
            )

            frame = build_report_frame(compared, causes, waterfall)
            excel_path: Path | None = None
            if len(frame) or settings.write_empty_report:
                excel_path = write_excel(
                    frame,
                    settings.output_dir / settings.excel_name.format(cutoff=cutoff),
                    title=f"Pricing reconciliation, cutoff {cutoff:%Y-%m-%d %H:%M:%S}",
                    description=f"{len(frame)} rows without a perfect match out of {len(compared)} compared.",
                )

            result = RunResult(
                run_id=run_id,
                cutoff=cutoff.isoformat(timespec="seconds"),
                pricing_dates=[d.isoformat() for _, d in dates],
                started_at=started.isoformat(timespec="seconds"),
                finished_at=datetime.now().isoformat(timespec="seconds"),
                duration_seconds=round(time.perf_counter() - clock, 2),
                rows_compared=len(compared),
                perfect=perfect,
                rounding=rounding,
                mismatch=mismatch,
                by_cause=by_cause,
                excel_path=str(excel_path) if excel_path else None,
                ecc_database=_describe(ecc_engine),
                s4_database=_describe(s4_engine),
            )
            summary_path = settings.output_dir / settings.summary_name.format(cutoff=cutoff)
            summary_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            result.summary_path = str(summary_path)

            removed = cleanup_old_outputs(
                settings.output_dir,
                [settings.excel_name, settings.summary_name],
                settings.keep_days,
            )
            log.info(
                "Run %s finished in %.1fs: compared=%d perfect=%d rounding=%d mismatch=%d causes=%s "
                "report=%s cleaned=%d",
                run_id,
                result.duration_seconds,
                result.rows_compared,
                perfect,
                rounding,
                mismatch,
                by_cause,
                result.excel_path,
                removed,
            )
            return result
        finally:
            if own_engines:
                ecc_engine.dispose()
                s4_engine.dispose()
