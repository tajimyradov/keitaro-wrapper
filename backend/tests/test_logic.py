import copy
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["CAMPAIGN_PREFIX"] = "test_ivanov_"

from app import services  # noqa: E402
from app.models import Base  # noqa: E402


class FakeKeitaro:
    def __init__(self, fail_second_stream=False):
        self.fail = fail_second_stream
        self.campaigns_, self.streams_, self.deleted = {}, [], []
        self._offers = [{"id": i, "name": f"Offer {i}"} for i in (1, 2, 3, 4, 5)]

    def offers(self): return self._offers
    def campaign(self, cid): return self.campaigns_[cid]
    def campaigns(self): return list(self.campaigns_.values())
    def create_campaign(self, p):
        c = {**p, "id": 10}; self.campaigns_[10] = c; return c
    def delete_campaign(self, cid): self.deleted.append(cid); self.campaigns_.pop(cid)
    def create_stream(self, p):
        if self.fail and p["position"] == 2:
            raise RuntimeError("boom")
        s = {**p, "id": 100 + len(self.streams_)}; self.streams_.append(s); return s
    def streams(self, cid): return copy.deepcopy([s for s in self.streams_ if s["campaign_id"] == cid])
    def stream(self, sid): return copy.deepcopy(next(s for s in self.streams_ if s["id"] == sid))
    def update_stream(self, sid, p): next(s for s in self.streams_ if s["id"] == sid).update(p)

    # хелперы тестов
    def seed(self, offers=((1, 34), (2, 33), (3, 33))):
        self.campaigns_[10] = {"id": 10, "name": "test_ivanov_c"}
        self.streams_ = [
            {"id": 1, "campaign_id": 10, "name": "AU → Google", "position": 1, "schema": "redirect"},
            {"id": 2, "campaign_id": 10, "name": "Offer", "position": 2, "schema": "landings",
             "offers": [{"offer_id": o, "share": s, "state": "active"} for o, s in offers]}]
    def remote(self): return {o["offer_id"]: o["share"] for o in self.streams_[1]["offers"]}


@pytest.fixture
def db():
    e = create_engine("sqlite://")
    Base.metadata.create_all(e)
    return sessionmaker(e)()


@pytest.fixture
def fk(monkeypatch):
    f = FakeKeitaro(); f.seed(); monkeypatch.setattr(services, "keitaro", f); return f


def offers(v, sid=2): return {o["offer_id"]: o for o in v["streams"][1 if sid == 2 else 0]["offers"]}
def shares(v): return {i: o["share"] for i, o in offers(v).items() if o["status"] == "active"}


def test_parse_geo():
    assert services.parse_geo("mx, au;RO mx") == ["MX", "AU", "RO"]
    for bad in ("", "USA", "M1"):
        with pytest.raises(HTTPException):
            services.parse_geo(bad)


def test_create_campaign_and_rollback(db, monkeypatch):
    f = FakeKeitaro(); monkeypatch.setattr(services, "keitaro", f)
    services.create_campaign(db, "c1", "MX,AU", 1, 1, 1, 1)
    geo, off = f.streams_
    assert len(f.campaigns_[10]["alias"]) == 6 and f.campaigns_[10]["alias"].isalnum()
    assert geo["action_type"] == "http" and off["action_type"] == "http"
    assert geo["filters"][0]["payload"] == ["MX", "AU"] and off["offers"][0]["share"] == 100
    f2 = FakeKeitaro(fail_second_stream=True); monkeypatch.setattr(services, "keitaro", f2)
    with pytest.raises(RuntimeError):
        services.create_campaign(db, "c2", "MX", 1, 1, 1, 1)
    assert f2.deleted == [10]


def test_fetch_stream_without_offers_is_plain(db, fk):
    v = services.fetch_streams(db, 10)
    assert v["streams"][0]["editable"] is False and v["streams"][0]["offers"] == []
    assert shares(v) == {1: 34, 2: 33, 3: 33} and not v["streams"][1]["dirty"]
    with pytest.raises(HTTPException):
        services.add_offer(db, 10, 1, 4)


def test_add_cancel_push(db, fk):
    services.fetch_streams(db, 10)
    v = services.add_offer(db, 10, 2, 4)
    assert shares(v) == {1: 25, 2: 25, 3: 25, 4: 25} and v["streams"][1]["dirty"]
    assert set(fk.remote()) == {1, 2, 3}                       # в Keitaro ещё 3
    v = services.cancel(db, 10, 2)
    assert 4 not in offers(v) and shares(v) == {1: 34, 2: 33, 3: 33} and not v["streams"][1]["dirty"]
    services.add_offer(db, 10, 2, 4)
    v = services.push(db, 10, 2)
    assert fk.remote() == {1: 25, 2: 25, 3: 25, 4: 25} and not v["streams"][1]["dirty"]


def test_remove_archive_bring_back(db, fk):
    services.fetch_streams(db, 10)
    v = services.remove_offer(db, 10, 2, 1)
    assert offers(v)[1]["status"] == "removed" and offers(v)[1]["share"] == 0
    assert shares(v) == {2: 50, 3: 50} and v["streams"][1]["dirty"]
    v = services.push(db, 10, 2)
    assert set(fk.remote()) == {2, 3} and offers(v)[1]["status"] == "removed" and not v["streams"][1]["dirty"]
    v = services.restore_offer(db, 10, 2, 1)
    assert v["streams"][1]["dirty"]
    services.push(db, 10, 2)
    assert fk.remote() == {1: 34, 2: 33, 3: 33}


def test_deleted_in_keitaro_goes_to_archive(db, fk):
    services.fetch_streams(db, 10)
    fk.streams_[1]["offers"] = [o for o in fk.streams_[1]["offers"] if o["offer_id"] != 3]
    v = services.fetch_streams(db, 10)
    assert offers(v)[3]["status"] == "removed" and not v["streams"][1]["dirty"]
    v = services.restore_offer(db, 10, 2, 3)
    assert shares(v) == {1: 34, 2: 33, 3: 33} or sum(shares(v).values()) == 100


def test_pinned_weight_survives_changes(db, fk):
    services.fetch_streams(db, 10)
    services.add_offer(db, 10, 2, 4); services.push(db, 10, 2)          # 25/25/25/25
    services.set_pin(db, 10, 2, 2, True)
    services.remove_offer(db, 10, 2, 1)
    v = services.restore_offer(db, 10, 2, 1)
    assert offers(v)[2]["share"] == 25 and offers(v)[2]["pinned"]
    services.remove_offer(db, 10, 2, 4)
    v = services.restore_offer(db, 10, 2, 4)
    services.remove_offer(db, 10, 2, 3)                                  # 25 закреплён, 2 свободных: 38/37
    v = services.restore_offer(db, 10, 2, 3)
    assert offers(v)[2]["share"] == 25 and sum(shares(v).values()) == 100
    v = services.remove_offer(db, 10, 2, 1)
    assert offers(v)[2]["share"] == 25 and sorted(shares(v).values()) == [25, 37, 38]
    v = services.push(db, 10, 2)
    assert fk.remote()[2] == 25 and sum(fk.remote().values()) == 100
    v = services.set_pin(db, 10, 2, 2, False)
    assert sorted(shares(v).values()) == [33, 33, 34]


def test_push_validation_and_foreign_campaign(db, fk):
    services.fetch_streams(db, 10)
    services.remove_offer(db, 10, 2, 1); services.remove_offer(db, 10, 2, 2); services.remove_offer(db, 10, 2, 3)
    with pytest.raises(HTTPException):
        services.push(db, 10, 2)                       # пустой поток
    fk.campaigns_[5] = {"id": 5, "name": "someone_else"}
    with pytest.raises(HTTPException) as e:
        services.fetch_streams(db, 5)
    assert e.value.status_code == 403


def test_too_many_pinned_rejected(db, fk):
    services.fetch_streams(db, 10)
    for o in (1, 2):
        services.set_pin(db, 10, 2, o, True)           # 34 + 33 = 67, на двух свободных остаётся 33
    services.add_offer(db, 10, 2, 4)
    services.add_offer(db, 10, 2, 5)                   # свободных 3 (3,4,5): 33 -> по 11
    with pytest.raises(HTTPException):
        for oid in range(6, 40):
            fk._offers.append({"id": oid, "name": f"O{oid}"}); services.add_offer(db, 10, 2, oid)


def test_fetch_falls_back_to_stream_details(db, fk):
    full = fk.streams
    fk.streams = lambda cid: [{k: v for k, v in s.items() if k != "offers"} for s in full(cid)]  # список без офферов
    v = services.fetch_streams(db, 10)
    assert v["streams"][1]["editable"] and shares(v) == {1: 34, 2: 33, 3: 33}


def test_nested_offer_format():
    assert services.remote_offers({"offers": [{"offer": {"id": 7}, "share": 40.0}, {"offer_id": 8, "share": 60}]}) == {7: 40, 8: 60}
