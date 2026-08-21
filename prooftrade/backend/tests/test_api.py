"""API surface, persistence and end-to-end reproducibility."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import app.storage as storage

    storage.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    # storage resolves DB_PATH at call time through its module-level import, so point
    # the module's own reference at a temporary file for the whole test module.
    original = storage._connect

    def _connect(path=None):
        return original(path or storage.DB_PATH)

    storage._connect = _connect
    with TestClient(app) as c:
        yield c
    storage._connect = original


def test_health_reports_versions_and_the_data_snapshot(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["symbols"] == 18
    assert len(body["data_snapshot_hash"]) == 16
    assert body["translator"] in ("rules", "anthropic")


def test_universe_lists_coverage(client):
    body = client.get("/api/universe").json()
    assert len(body["symbols"]) == 18
    assert all(s["bars"] > 5000 for s in body["symbols"])


def test_schema_exposes_the_dsl_and_the_indicator_registry(client):
    body = client.get("/api/schema").json()
    assert body["dsl_version"] == "1.0.0"
    assert len(body["indicators"]) >= 20
    assert "properties" in body["strategy_schema"]


def test_examples_are_all_parseable(client):
    for example in client.get("/api/examples").json():
        response = client.post("/api/parse", json={"text": example["text"]})
        assert response.status_code == 200, f"{example['id']}: {response.text[:200]}"


def test_parse_rejects_nonsense_with_a_useful_message(client):
    response = client.post("/api/parse", json={"text": "buy the dip"})
    assert response.status_code == 422
    assert "entry condition" in response.json()["detail"]


def test_parse_rejects_an_empty_body(client):
    assert client.post("/api/parse", json={"text": ""}).status_code == 422


def test_backtest_rejects_a_symbol_outside_the_universe(client):
    strategy = client.post("/api/parse", json={
        "text": "Buy SPY when RSI is below 30, sell after 10 days"}).json()["strategy"]
    strategy["universe"] = ["TSLA"]
    response = client.post("/api/backtest", json={"strategy": strategy, "config": {}})
    assert response.status_code == 422
    assert "TSLA" in response.json()["detail"]


def test_backtest_rejects_an_unknown_indicator(client):
    bad = {
        "universe": ["SPY"],
        "entry": {"logic": "all", "conditions": [{
            "left": {"kind": "indicator", "name": "insider_knowledge"},
            "op": "<", "right": {"kind": "constant", "value": 30}}]},
        "risk": {"max_holding_days": 5},
    }
    assert client.post("/api/backtest", json={"strategy": bad}).status_code == 422


@pytest.fixture(scope="module")
def report(client):
    parsed = client.post("/api/parse", json={
        "text": "Buy when RSI(14) drops below 32 and the price is above the 200-day "
                "moving average, sell when RSI goes above 55 or after 20 days. 8% stop loss."
    }).json()
    response = client.post("/api/backtest",
                           json={"strategy": parsed["strategy"], "config": {}})
    assert response.status_code == 200, response.text[:400]
    return response.json()


def test_the_report_contains_every_section_the_ui_needs(report):
    for key in ("summary", "benchmark", "series", "trades", "per_symbol", "per_year",
                "per_regime", "concentration", "validation", "evidence", "diagnostics",
                "reproducibility", "result_hash", "run_id", "strategy_render"):
        assert key in report, f"missing {key}"
    assert set(report["validation"]) == {
        "split", "walk_forward", "cost_sensitivity", "parameter_robustness"}
    assert len(report["series"]) == report["summary"]["days"]


def test_the_report_is_reproducible(client, report):
    again = client.post("/api/backtest", json={
        "strategy": report["strategy"], "config": report["config"]}).json()
    assert again["result_hash"] == report["result_hash"]
    assert again["run_id"] == report["run_id"]


def test_changing_the_cost_assumption_changes_the_result_hash(client, report):
    config = dict(report["config"], slippage_bps=25.0)
    other = client.post("/api/backtest",
                        json={"strategy": report["strategy"], "config": config}).json()
    assert other["result_hash"] != report["result_hash"]
    assert other["summary"]["total_return_pct"] < report["summary"]["total_return_pct"]


def test_runs_are_listed_and_reloadable(client, report):
    listed = client.get("/api/runs").json()
    assert any(r["run_id"] == report["run_id"] for r in listed)
    reloaded = client.get(f"/api/runs/{report['run_id']}").json()
    assert reloaded["result_hash"] == report["result_hash"]
    assert reloaded["trades"] == report["trades"]


def test_an_unknown_run_is_a_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_the_evidence_report_always_evaluates_the_required_warnings(report):
    ids = {w["id"] for w in report["evidence"]["warnings"]}
    assert "universe_hindsight" in ids and "single_data_source" in ids
    assert report["evidence"]["grade"] in list("ABCDF")
    assert 0 <= report["evidence"]["score"] <= 100
    assert len(report["evidence"]["components"]) == 6
