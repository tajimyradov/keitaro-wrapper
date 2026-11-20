import time

import httpx

from .config import settings


class KeitaroError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"Keitaro {status}: {body}")


class Keitaro:
    def __init__(self):
        self.client = httpx.Client(
            base_url=f"{settings.keitaro_url.rstrip('/')}/admin_api/v1",
            headers={"Api-Key": settings.keitaro_api_key},
            timeout=30,
        )
        self._offers_cache: tuple[float, list] = (0, [])

    def _req(self, method: str, path: str, **kw):
        try:
            r = self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise KeitaroError(503, f"Keitaro недоступен: {e}")
        if r.status_code >= 400:
            raise KeitaroError(r.status_code, r.text[:500])
        return r.json() if r.content else None

    # справочники
    def domains(self): return self._req("GET", "/domains")
    def groups(self, type_="campaigns"): return self._req("GET", "/groups", params={"type": type_})
    def traffic_sources(self): return self._req("GET", "/traffic_sources")

    def offers(self):
        ts, data = self._offers_cache
        if time.time() - ts > 60:
            data = self._req("GET", "/offers")
            self._offers_cache = (time.time(), data)
        return data

    # кампании и потоки
    def campaigns(self): return self._req("GET", "/campaigns")
    def campaign(self, cid): return self._req("GET", f"/campaigns/{cid}")
    def create_campaign(self, payload): return self._req("POST", "/campaigns", json=payload)
    def delete_campaign(self, cid): return self._req("DELETE", f"/campaigns/{cid}")
    def stream(self, sid): return self._req("GET", f"/streams/{sid}")
    def streams(self, cid): return self._req("GET", f"/campaigns/{cid}/streams")
    def create_stream(self, payload): return self._req("POST", "/streams", json=payload)
    def update_stream(self, sid, payload): return self._req("PUT", f"/streams/{sid}", json=payload)

    def report(self, payload): return self._req("POST", "/report/build", json=payload)


keitaro = Keitaro()
