import random
import re
import string

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import full_name, settings
from .keitaro import KeitaroError, keitaro
from .models import ActionLog, CampaignLog, Stream, StreamOffer

GEO_RE = re.compile(r"^[A-Z]{2}$")


def make_alias(n: int = 6) -> str:
    """Keitaro требует alias кампании (идентификатор в ссылке), например wVqN1R."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def remote_offers(stream: dict) -> dict[int, int]:
    """{offer_id: share} из потока Keitaro; терпимо к вложенному формату {"offer": {"id": ..}}."""
    out = {}
    for o in stream.get("offers") or []:
        oid = o.get("offer_id") or (o.get("offer") or {}).get("id")
        if oid is not None:
            out[int(oid)] = int(round(o.get("share") or 0))
    return out


def parse_geo(raw: str) -> list[str]:
    """'mx, au;RO' -> ['MX','AU','RO'] (ISO 3166-1 alpha-2, без дублей)."""
    codes = [c.strip().upper() for c in re.split(r"[,\s;]+", raw or "") if c.strip()]
    bad = [c for c in codes if not GEO_RE.match(c)]
    if not codes or bad:
        raise HTTPException(422, f"Некорректный гео-код: {', '.join(bad) or 'пусто'}. Нужны 2 буквы, например MX,AU,RO")
    return list(dict.fromkeys(codes))


def search_offers(q: str, limit: int = 20) -> list[dict]:
    q = (q or "").strip().lower()
    res = [{"id": o["id"], "name": o["name"]} for o in keitaro.offers()
           if not q or q in o["name"].lower() or q == str(o["id"])]
    return res[:limit]


def create_campaign(db: Session, name: str, geo_raw: str, offer_id: int,
                    domain_id: int, group_id: int, traffic_source_id: int) -> dict:
    geo = parse_geo(geo_raw)
    if not any(o["id"] == offer_id for o in keitaro.offers()):
        raise HTTPException(422, f"Оффер {offer_id} не найден в Keitaro")
    name = full_name(name)
    if len(name) <= len(settings.campaign_prefix):
        raise HTTPException(422, "Укажите название кампании")

    campaign = keitaro.create_campaign({
        "name": name,
        "alias": make_alias(),
        "type": "position",            # ротация потоков «по позиции»
        "domain_id": domain_id,
        "group_id": group_id,
        "traffic_source_id": traffic_source_id,
        "cost_type": "CPC", "cost_value": 0, "cost_currency": "USD", "cost_auto": True,
        "state": "active",
    })
    cid = campaign["id"]
    try:
        keitaro.create_stream({
            "campaign_id": cid, "name": f"{','.join(geo)} → Google",
            "type": "regular", "position": 1, "schema": "redirect",
            "action_type": "http", "action_payload": "https://google.com",
            "filters": [{"name": "country", "mode": "accept", "payload": geo}],
            "collect_clicks": True, "state": "active",
        })
        keitaro.create_stream({
            "campaign_id": cid, "name": "Offer",
            "type": "regular", "position": 2, "schema": "landings",
            # Keitaro требует action_type у любого потока (как у существующих кампаний на стенде)
            "action_type": "http", "action_payload": f"{settings.keitaro_url.rstrip('/')}/offer/{offer_id}",
            "offers": [{"offer_id": offer_id, "share": 100, "state": "active"}],
            "collect_clicks": True, "state": "active",
        })
    except Exception:
        keitaro.delete_campaign(cid)   # не оставляем полусозданную кампанию
        raise
    db.add(CampaignLog(keitaro_id=cid, name=name, geo=",".join(geo), offer_id=offer_id))
    db.commit()
    return {"id": cid, "name": name, "geo": geo}


# ───────────── редактор ─────────────

def _offer_name(offer_id: int) -> str:
    return next((o["name"] for o in keitaro.offers() if o["id"] == offer_id), f"#{offer_id}")


def _own_campaign(cid: int) -> dict:
    c = keitaro.campaign(cid)
    if not c["name"].startswith(settings.campaign_prefix):
        raise HTTPException(403, f"Можно редактировать только кампании с префиксом {settings.campaign_prefix}")
    return c


def list_campaigns() -> list[dict]:
    return [{"id": c["id"], "name": c["name"]} for c in keitaro.campaigns()
            if c["name"].startswith(settings.campaign_prefix)]


def recalc(links) -> None:
    """Пересчёт весов. Закреплённые не трогаем, остаток 100% делим поровну между
    незакреплёнными активными (остаток от деления получают первые). Удалённые = 0%."""
    active = [l for l in links if l.status == "active"]
    for l in links:
        if l.status != "active":
            l.share = 0
    pinned = [l for l in active if l.pin_value is not None]
    free = [l for l in active if l.pin_value is None]
    for l in pinned:
        l.share = l.pin_value
    rest = 100 - sum(l.pin_value for l in pinned)
    if free:
        if rest < len(free):
            raise HTTPException(422, f"Закреплённые веса занимают {100 - rest}%: на {len(free)} оффер(ов) не остаётся доли. Открепите вес")
        base, extra = divmod(rest, len(free))
        for i, l in enumerate(free):
            l.share = base + (1 if i < extra else 0)


def _dirty(s: Stream) -> bool:
    return any(l.is_new or l.status != l.synced_status
               or (l.status == "active" and l.share != l.synced_share) for l in s.links)


def _log(db, action, cid, sid=None, oid=None, details=None):
    db.add(ActionLog(action=action, campaign_id=cid, stream_id=sid, offer_id=oid, details=details))


def view(db: Session, cid: int) -> dict:
    rows = db.scalars(select(Stream).where(Stream.campaign_id == cid)
                      .order_by(Stream.position, Stream.id)).all()
    out = []
    for s in rows:
        links = sorted(s.links, key=lambda l: (l.status != "active", l.id))
        out.append({
            "id": s.keitaro_id, "name": s.name, "position": s.position,
            "editable": bool(s.links),            # потоки без офферов показываем только как поток
            "dirty": _dirty(s),
            "share_total": sum(l.share for l in s.links if l.status == "active"),
            "offers": [{"offer_id": l.offer_id, "name": l.name, "status": l.status, "share": l.share,
                        "pinned": l.pin_value is not None, "is_new": l.is_new} for l in links],
        })
    return {"streams": out}


def _stream(db, cid, sid) -> Stream:
    s = db.scalar(select(Stream).where(Stream.campaign_id == cid, Stream.keitaro_id == sid))
    if not s:
        raise HTTPException(404, "Поток не найден. Нажмите «Получить потоки»")
    return s


def _link(s: Stream, offer_id: int) -> StreamOffer:
    l = next((x for x in s.links if x.offer_id == offer_id), None)
    if not l:
        raise HTTPException(404, "Оффера нет в потоке")
    return l


def _apply(db, s):
    try:
        recalc(s.links)
    except HTTPException:
        db.rollback()
        raise


def fetch_streams(db: Session, cid: int) -> dict:
    """Синхронизация с Keitaro. Неопубликованные правки сбрасываются; офферы,
    которых больше нет в Keitaro, остаются у нас в архиве (removed)."""
    _own_campaign(cid)
    seen = set()
    for r in keitaro.streams(cid):
        if r.get("offers") is None:            # список мог прийти без офферов: берём детали потока
            try:
                r = keitaro.stream(r["id"])
            except KeitaroError:
                pass
        seen.add(r["id"])
        s = db.scalar(select(Stream).where(Stream.keitaro_id == r["id"]))
        if not s:
            s = Stream(keitaro_id=r["id"], campaign_id=cid)
            db.add(s)
        s.campaign_id, s.name, s.position = cid, r["name"], r.get("position") or 0
        remote = remote_offers(r)
        for l in list(s.links):
            if l.offer_id in remote:
                l.status = l.synced_status = "active"
                l.share = l.synced_share = remote[l.offer_id]
                l.is_new = False
            elif l.is_new:
                s.links.remove(l)
            else:
                l.status = l.synced_status = "removed"
                l.share = l.synced_share = 0
        known = {l.offer_id for l in s.links}
        for oid, share in remote.items():
            if oid not in known:
                s.links.append(StreamOffer(offer_id=oid, name=_offer_name(oid), status="active", share=share,
                                           synced_status="active", synced_share=share, is_new=False))
    for s in db.scalars(select(Stream).where(Stream.campaign_id == cid)).all():
        if s.keitaro_id not in seen:
            db.delete(s)
    _log(db, "fetch", cid)
    db.commit()
    return view(db, cid)


def add_offer(db: Session, cid: int, sid: int, offer_id: int) -> dict:
    s = _stream(db, cid, sid)
    if not s.links:
        raise HTTPException(409, "В этом потоке нет офферов, редактировать его нельзя")
    link = next((l for l in s.links if l.offer_id == offer_id), None)
    if link and link.status == "active":
        raise HTTPException(409, "Оффер уже есть в потоке")
    if not any(o["id"] == offer_id for o in keitaro.offers()):
        raise HTTPException(422, f"Оффер {offer_id} не найден в Keitaro")
    if link:                                   # оффер из архива = «вернуть»
        link.status, link.share = "active", link.pin_value or 0
    else:
        s.links.append(StreamOffer(offer_id=offer_id, name=_offer_name(offer_id), status="active", share=0,
                                   is_new=True, synced_status="removed", synced_share=0))
    _apply(db, s)
    _log(db, "add", cid, sid, offer_id)
    db.commit()
    return view(db, cid)


def remove_offer(db: Session, cid: int, sid: int, offer_id: int) -> dict:
    s = _stream(db, cid, sid)
    l = _link(s, offer_id)
    if l.status != "active":
        raise HTTPException(409, "Оффер уже удалён")
    if l.is_new:
        s.links.remove(l)                      # ещё не публиковался: просто отменяем добавление
    else:
        l.status, l.share = "removed", 0
    _apply(db, s)
    _log(db, "remove", cid, sid, offer_id)
    db.commit()
    return view(db, cid)


def restore_offer(db: Session, cid: int, sid: int, offer_id: int) -> dict:
    s = _stream(db, cid, sid)
    l = _link(s, offer_id)
    if l.status == "active":
        raise HTTPException(409, "Оффер уже активен")
    l.status, l.share = "active", l.pin_value or 0
    _apply(db, s)
    _log(db, "restore", cid, sid, offer_id)
    db.commit()
    return view(db, cid)


def set_pin(db: Session, cid: int, sid: int, offer_id: int, pinned: bool) -> dict:
    s = _stream(db, cid, sid)
    l = _link(s, offer_id)
    if pinned:
        if l.status != "active" or l.share <= 0:
            raise HTTPException(409, "Закрепить можно только активный оффер с долей больше 0")
        l.pin_value = l.share
    else:
        l.pin_value = None
        _apply(db, s)
    _log(db, "pin" if pinned else "unpin", cid, sid, offer_id, f"share={l.share}")
    db.commit()
    return view(db, cid)


def push(db: Session, cid: int, sid: int) -> dict:
    s = _stream(db, cid, sid)
    active = [l for l in s.links if l.status == "active"]
    if not active:
        raise HTTPException(422, "В потоке должен остаться хотя бы один активный оффер")
    total = sum(l.share for l in active)
    if total != 100 or any(l.share <= 0 for l in active):
        raise HTTPException(422, f"Сумма долей {total}%, нужно 100% (у каждого оффера больше 0). Откройте закрепления")
    keitaro.update_stream(sid, {"offers": [{"offer_id": l.offer_id, "share": l.share, "state": "active"}
                                           for l in active]})
    for l in s.links:
        l.is_new = False
        l.synced_status = l.status
        l.synced_share = l.share if l.status == "active" else 0
    _log(db, "push", cid, sid, details=",".join(f"{l.offer_id}:{l.share}" for l in active))
    db.commit()
    return view(db, cid)


def cancel(db: Session, cid: int, sid: int) -> dict:
    s = _stream(db, cid, sid)
    for l in list(s.links):
        if l.is_new:
            s.links.remove(l)                  # привязка удаляется
        else:
            l.status, l.share = l.synced_status, l.synced_share
    _log(db, "cancel", cid, sid)
    db.commit()
    return view(db, cid)
