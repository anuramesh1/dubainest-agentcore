"""api routes against a fake DynamoDB (moto) holding the real 20-listing seed.

    .venv/bin/pytest api
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data"))
from mock_properties import PROPERTIES  # noqa: E402

CDN = "https://assets.example.cloudfront.net/media/photos"


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed", REPO / "scripts" / "seed_properties.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_PROPERTIES_TABLE", "props-test")
    monkeypatch.setenv("DYNAMODB_LEADS_TABLE", "leads-test")
    monkeypatch.setenv("MEDIA_BASE_URL", CDN)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        props = ddb.create_table(
            TableName="props-test",
            KeySchema=[{"AttributeName": "propertyId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "propertyId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="leads-test",
            KeySchema=[{"AttributeName": "leadId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "leadId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        to_item = _seed_module().property_to_item  # the same mapping the seed script writes
        for p in PROPERTIES:
            props.put_item(Item=to_item(dict(p)))

        from api.app import app
        from api.properties import CATALOGUE

        CATALOGUE.clear()
        yield TestClient(app)
        CATALOGUE.clear()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_list_all_sorted_by_price(client):
    rows = client.get("/api/properties").json()
    assert len(rows) == 20
    prices = [r["price"] for r in rows]
    assert prices == sorted(prices)
    assert set(rows[0]) == set(PROPERTIES[0])  # same fields the UI's Property has


def test_filters_match_seed(client):
    cases = [
        ({"developer": "DAMAC"}, lambda p: p["developer"] == "DAMAC"),
        ({"area": "Dubailand"}, lambda p: p["area"] == "Dubailand"),
        ({"type": "townhouse"}, lambda p: p["type"] == "townhouse"),
        ({"bedrooms": 2}, lambda p: p["bedrooms"] == 2),
        ({"minPrice": 2_000_000, "maxPrice": 5_000_000},
         lambda p: 2_000_000 <= p["price"] <= 5_000_000),
        ({"developer": "Emaar", "bedrooms": 3},
         lambda p: p["developer"] == "Emaar" and p["bedrooms"] == 3),
    ]
    for params, keep in cases:
        got = {r["id"] for r in client.get("/api/properties", params=params).json()}
        want = {p["id"] for p in PROPERTIES if keep(p)}
        assert got == want, params
        assert got, f"filter {params} should match something in the seed"


def test_facets(client):
    f = client.get("/api/properties/facets").json()
    assert f["developers"] == ["DAMAC", "Emaar", "Nakheel"]
    assert "Dubailand" in f["areas"]
    assert f["bedrooms"] == sorted({p["bedrooms"] for p in PROPERTIES})
    assert set(f["types"]) == {p["type"] for p in PROPERTIES}


def test_detail_and_media_rewrite(client):
    r = client.get("/api/properties/10").json()
    assert r["title"] == "DAMAC Islands — Dubailand"
    assert r["roi"] == 0 and isinstance(r["lat"], float)
    assert r["images"] == [f"{CDN}/Damac%20islands.jpeg"]


def test_detail_404(client):
    assert client.get("/api/properties/999").status_code == 404


def test_property_intro(client):
    body = client.get("/api/chat/property-intro/10").json()
    assert body["intro"].startswith("**DAMAC Islands — Dubailand**")
    assert "Off-plan, handover 2028." in body["intro"]
    assert "AED 2,250,000" in body["intro"]
    assert len(body["suggestions"]) == 4
    assert client.get("/api/chat/property-intro/999").status_code == 404


ENQUIRY = {
    "property_id": "10",
    "name": "Test Buyer",
    "email": "buyer@example.com",
    "phone": "+971500000000",
    "message": "Is a viewing possible next week?",
}


def test_enquiry_saved_to_leads(client):
    r = client.post("/api/enquiries", json=ENQUIRY)
    assert r.status_code == 201
    body = r.json()
    lead = boto3.resource("dynamodb", region_name="us-east-1").Table("leads-test").get_item(
        Key={"leadId": body["id"]}
    )["Item"]
    assert lead["propertyId"] == "10"
    assert lead["email"] == "buyer@example.com"
    assert lead["source"] == "enquiry_form"
    assert lead["createdAt"].startswith(body["created_at"][:19])


@pytest.mark.parametrize(
    "change",
    [
        {"property_id": "999"},
        {"email": "not-an-email"},
        {"name": "   "},
        {"message": "x" * 2001},
    ],
)
def test_enquiry_rejected(client, change):
    r = client.post("/api/enquiries", json={**ENQUIRY, **change})
    assert r.status_code in (404, 422)
    leads = boto3.resource("dynamodb", region_name="us-east-1").Table("leads-test")
    assert leads.scan()["Count"] == 0


def test_catalogue_cache_reuses_one_scan(client, monkeypatch):
    from api.properties import Catalogue

    calls = []
    real = Catalogue._scan
    monkeypatch.setattr(Catalogue, "_scan", staticmethod(lambda: calls.append(1) or real()))
    for path in ("/api/properties", "/api/properties/facets", "/api/properties/1"):
        assert client.get(path).status_code == 200
    assert len(calls) == 1
