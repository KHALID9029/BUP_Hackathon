import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config
from app.graph import build_graph
from app.schemas import LLMInterpretation, LLMDirective
from tests.conftest import FakeLLM

SAMPLES = Path(__file__).resolve().parent.parent / "Problem_doc" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


@pytest.fixture
def sample_input():
    cases = json.loads(SAMPLES.read_text())["cases"]
    return cases[0]["input"]


@pytest.fixture(autouse=True)
def reset_graph():
    main_module._graph = None
    yield
    main_module._graph = None


@pytest.fixture
def client():
    return TestClient(main_module.app)


def install_fake_graph(responses):
    main_module._graph = build_graph(llm=FakeLLM(responses))


def test_health_never_touches_llm(client, monkeypatch):
    monkeypatch.setattr(config.settings, "OPENROUTER_API_KEY", None)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_malformed_json_is_400(client):
    resp = client.post("/optimize-energy", content="{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


def test_23_hours_is_400(client, sample_input):
    bad = dict(sample_input, hours=sample_input["hours"][:23])
    resp = client.post("/optimize-energy", json=bad)
    assert resp.status_code == 400


def test_duplicate_hour_is_400(client, sample_input):
    hours = sample_input["hours"][:23] + [sample_input["hours"][0]]
    bad = dict(sample_input, hours=hours)
    resp = client.post("/optimize-energy", json=bad)
    assert resp.status_code == 400


def test_four_notes_is_400(client, sample_input):
    bad = dict(sample_input, operator_notes=sample_input["operator_notes"] + ["a", "b"])
    resp = client.post("/optimize-energy", json=bad)
    assert resp.status_code == 400


def test_empty_note_is_400(client, sample_input):
    bad = dict(sample_input, operator_notes=["   "])
    resp = client.post("/optimize-energy", json=bad)
    assert resp.status_code == 400


def test_missing_key_gives_controlled_500_on_post_only(client, sample_input, monkeypatch):
    monkeypatch.setattr(config.settings, "OPENROUTER_API_KEY", None)
    resp = client.get("/health")
    assert resp.status_code == 200
    resp = client.post("/optimize-energy", json=sample_input)
    assert resp.status_code == 500
    assert resp.json() == {"error": "llm_not_configured"}


def test_llm_exception_degrades_to_200_with_no_op(client, sample_input):
    install_fake_graph([RuntimeError("boom"), RuntimeError("boom")])
    resp = client.post("/optimize-energy", json=sample_input)
    assert resp.status_code == 200
    body = resp.json()
    assert all(d["directive_type"] == "no_op" for d in body["directive_interpretation"])
    assert len(body["hourly_plan"]) == 24


def test_extra_fields_ignored_and_scenario_id_echoed(client, sample_input):
    install_fake_graph([LLMInterpretation(directives=[
        LLMDirective(note_index=i, directive_type="no_op", explanation="x")
        for i in range(len(sample_input["operator_notes"]))
    ])])
    payload = dict(sample_input, unexpected_field="should be ignored")
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["scenario_id"] == sample_input["scenario_id"]


def test_response_never_contains_api_key(client, sample_input, monkeypatch):
    monkeypatch.setattr(config.settings, "OPENROUTER_API_KEY", "sk-or-v1-secret-should-not-leak")
    install_fake_graph([LLMInterpretation(directives=[
        LLMDirective(note_index=i, directive_type="no_op", explanation="x")
        for i in range(len(sample_input["operator_notes"]))
    ])])
    resp = client.post("/optimize-energy", json=sample_input)
    assert "sk-or-v1-secret-should-not-leak" not in resp.text


def test_ui_does_not_change_api_routes(client):
    # The optional web UI must never shadow or alter the graded endpoints.
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/optimize-energy").status_code == 405
    assert client.post("/", json={}).status_code in (404, 405)


@pytest.mark.skipif(not (main_module._ui / "index.html").is_file(), reason="frontend not built")
def test_ui_served_when_built(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_sloppy_paths_are_normalized(client, sample_input):
    # A judge joining "https://host/" + "/optimize-energy" must still reach the endpoint, not 404/307.
    install_fake_graph([LLMInterpretation(directives=[])])
    assert client.get("http://testserver//health").json() == {"status": "ok"}
    assert client.get("/health/").json() == {"status": "ok"}
    for path in ("http://testserver//optimize-energy", "/optimize-energy/"):
        resp = client.post(path, json=sample_input, follow_redirects=False)
        assert resp.status_code == 200, path
