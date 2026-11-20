from pathlib import Path

from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import services
from .config import settings
from .db import get_db
from .keitaro import KeitaroError, keitaro

app = FastAPI(title="Keitaro wrapper")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@app.exception_handler(KeitaroError)
def keitaro_error(_, exc: KeitaroError):
    code = exc.status if exc.status in (401, 403, 404, 422) else 502
    return JSONResponse({"detail": f"Keitaro: {exc.body}"}, status_code=code)


class CampaignIn(BaseModel):
    name: str
    geo: str
    offer_id: int
    domain_id: int
    group_id: int
    traffic_source_id: int


class OfferIn(BaseModel):
    offer_id: int


class PinIn(BaseModel):
    pinned: bool


def _named(call, errors, label, keep=None):
    try:
        items = call()
        if keep:
            kept = [x for x in items if keep(x)]
            items = kept or items
        return [{"id": x["id"], "name": x.get("name") or x.get("domain") or f"#{x['id']}"} for x in items]
    except Exception as e:  # одна упавшая ручка не должна ломать остальные
        errors[label] = str(e)[:300]
        return []


@app.get("/api/meta")
def meta():
    errors: dict[str, str] = {}
    return {
        "prefix": settings.campaign_prefix,
        "domains": _named(keitaro.domains, errors, "domains"),
        "groups": _named(keitaro.groups, errors, "groups",
                         keep=lambda g: str(g.get("type", "campaigns")).startswith("campaign")),
        "sources": _named(keitaro.traffic_sources, errors, "sources"),
        "errors": errors,
    }


@app.get("/api/debug/campaigns/{cid}/streams")
def debug_streams(cid: int):
    """Сырой ответ Keitaro по потокам (для отладки формата API); только кампании с вашим префиксом."""
    services._own_campaign(cid)
    return keitaro.streams(cid)


@app.get("/api/offers")
def offers(q: str = Query("")):
    return services.search_offers(q)


@app.post("/api/campaigns", status_code=201)
def create_campaign(body: CampaignIn, db: Session = Depends(get_db)):
    return services.create_campaign(db, body.name, body.geo, body.offer_id,
                                    body.domain_id, body.group_id, body.traffic_source_id)


@app.get("/api/campaigns")
def campaigns():
    return services.list_campaigns()


@app.get("/api/campaigns/{cid}/streams")
def streams(cid: int, db: Session = Depends(get_db)):
    return services.view(db, cid)


@app.post("/api/campaigns/{cid}/fetch")
def fetch(cid: int, db: Session = Depends(get_db)):
    return services.fetch_streams(db, cid)


_P = "/api/campaigns/{cid}/streams/{sid}"


@app.post(_P + "/offers")
def add_offer(cid: int, sid: int, body: OfferIn, db: Session = Depends(get_db)):
    return services.add_offer(db, cid, sid, body.offer_id)


@app.delete(_P + "/offers/{offer_id}")
def remove_offer(cid: int, sid: int, offer_id: int, db: Session = Depends(get_db)):
    return services.remove_offer(db, cid, sid, offer_id)


@app.post(_P + "/offers/{offer_id}/restore")
def restore_offer(cid: int, sid: int, offer_id: int, db: Session = Depends(get_db)):
    return services.restore_offer(db, cid, sid, offer_id)


@app.put(_P + "/offers/{offer_id}/pin")
def pin_offer(cid: int, sid: int, offer_id: int, body: PinIn, db: Session = Depends(get_db)):
    return services.set_pin(db, cid, sid, offer_id, body.pinned)


@app.post(_P + "/push")
def push(cid: int, sid: int, db: Session = Depends(get_db)):
    return services.push(db, cid, sid)


@app.post(_P + "/cancel")
def cancel(cid: int, sid: int, db: Session = Depends(get_db)):
    return services.cancel(db, cid, sid)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
