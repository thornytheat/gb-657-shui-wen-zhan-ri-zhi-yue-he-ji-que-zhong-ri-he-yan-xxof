"""测试固定装置：临时库、固定时区、样例目录。

时间断言只用样例里的固定时刻，不读 datetime.now()。
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
if str(FIXTURES.parent) not in sys.path:
    sys.path.insert(0, str(FIXTURES.parent))

from app import config                                    # noqa: E402
from app.main import create_app                           # noqa: E402
from app.modules.m6_batches import service as batch_service  # noqa: E402
from app.store import Store                               # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"


@pytest.fixture(autouse=True)
def fixed_timezone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "hydrocalendar.sqlite"),
                           sample_dir=str(SAMPLE_DIR))


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def run_result(settings, store) -> dict:
    return batch_service.run(settings, store, sample="readings_dirty.csv")


@pytest.fixture()
def batch_id(run_result) -> str:
    return run_result["batch"]["batch_id"]


@pytest.fixture()
def client_with_run(client) -> TestClient:
    response = client.post("/api/runs", json={"sample": "readings_dirty.csv"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done"
    return client


@pytest.fixture()
def sample_dir() -> pathlib.Path:
    return SAMPLE_DIR
