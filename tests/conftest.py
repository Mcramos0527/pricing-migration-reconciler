
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from pricing_reconciler.config import AppConfig, load_config
from pricing_reconciler.synthetic.generator import SyntheticDataset, generate, seed_databases

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
AS_OF = date(2026, 10, 18)


def temp_config(tmp_path: Path) -> AppConfig:
    config = load_config(REPO_CONFIG)
    settings = dataclasses.replace(
        config.settings, output_dir=tmp_path / "output", log_dir=tmp_path / "logs"
    )
    return dataclasses.replace(config, settings=settings)


@pytest.fixture(scope="session")
def base_config() -> AppConfig:
    return load_config(REPO_CONFIG)


class Demo:

    def __init__(self, folder: Path, plant: bool = True) -> None:
        self.folder = folder
        self.dataset: SyntheticDataset = generate(AS_OF, plant=plant)
        self.config = temp_config(folder)
        self.ecc_url = f"sqlite:///{folder / 'ecc47.db'}"
        self.s4_url = f"sqlite:///{folder / 's4hana.db'}"
        self.ecc_engine = create_engine(self.ecc_url)
        self.s4_engine = create_engine(self.s4_url)
        seed_databases(self.dataset, self.ecc_engine, self.s4_engine, self.config.settings.tables)

    def close(self) -> None:
        self.ecc_engine.dispose()
        self.s4_engine.dispose()


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory):
    instance = Demo(tmp_path_factory.mktemp("demo"))
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def clean_demo(tmp_path_factory: pytest.TempPathFactory):
    instance = Demo(tmp_path_factory.mktemp("clean"), plant=False)
    yield instance
    instance.close()
