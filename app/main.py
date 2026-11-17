"""A local Keitaro campaign creator and draft-based offer editor."""
import copy
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import Session, StreamDraft, migrate
from app.keitaro import Keitaro, KeitaroError
from app.weights import rebalance

ROOT = Path(__file__).parent


@asynccontextmanager
async def lifespan(app):
    migrate()
    yield


app = FastAPI(title="Adrobot test wrapper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.exception_handler(KeitaroError)
async def upstream_error(request: Request, exc: KeitaroError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)


class AddOffer(BaseModel):
    offer_id: int = Field(gt=0)
    name: str = ""


class PinOffer(BaseModel):
    pinned: bool
    share: int | None = Field(default=None, ge=0, le=100)


def api():
    return Keitaro()


def normalized_offers(stream):
    """A stable, narrow representation for change detection."""
    return sorted(
        (int(x["offer_id"]), int(x.get("share", 0)), x.get("state", "active"))
        for x in stream.get("offers", [])
    )


def serialise(row):
    return {"stream_id": row.stream_id, "campaign_id": row.campaign_id,
            "name": row.name, "dirty": row.dirty, **row.draft}


def row_or_404(db, stream_id):
    row = db.get(StreamDraft, stream_id)
    if not row:
        raise HTTPException(404, "Fetch the campaign streams first.")
    return row


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/options")
def options():
    client = api()
    return {"campaigns": client.get("/campaigns"), "offers": client.get("/offers"),
            "domains": client.get("/domains"), "groups": client.get("/groups", params={"type": "campaigns"}),
            "sources": client.get("/traffic_sources")}


@app.post("/api/campaigns")
def create_campaign(data: CampaignIn):
    # Prevent accidental use in any campaign outside the employer's sandbox naming rule.
    name = data.name.strip()
    if not re.fullmatch(r"test_karklin(?:s|sh)_[\w-]+", name, re.IGNORECASE):
        raise HTTPException(422, "Campaign name must start with test_karklins_ and contain a suffix.")
    country = data.country.upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise HTTPException(422, "Country must be a two-letter code, for example GB.")
    client = api()
    # A unique alias avoids collisions without overwriting an existing campaign.
    campaign = client.post("/campaigns", {"name": name, "alias": "test_karklins_" + secrets.token_hex(5),
        "type": "position", "domain_id": data.domain_id, "group_id": data.group_id,
        "traffic_source_id": data.traffic_source_id})
    campaign_id = campaign["id"]
    try:
        client.post("/streams", {"campaign_id": campaign_id, "name": "Country to Google", "type": "regular",
            "position": 1, "schema": "redirect", "action_type": "http", "action_payload": "https://www.google.com/",
            "filters": [{"name": "country", "mode": "accept", "payload": [country]}]})
        client.post("/streams", {"campaign_id": campaign_id, "name": "Offers", "type": "default",
            "position": 2, "schema": "landings", "action_type": "http",
            "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}]})
    except KeitaroError as exc:
        # Preserve the partial record rather than deleting something in the shared tracker.
        raise HTTPException(502, f"Campaign {campaign_id} was created but configuring its flows failed. Check it in Keitaro before retrying. {exc}") from exc
    return {"id": campaign_id, "name": name}


@app.post("/api/campaigns/{campaign_id}/fetch")
def fetch_campaign(campaign_id: int):
    client = api()
    streams = client.get(f"/campaigns/{campaign_id}/streams")
    offers_by_id = {int(x["id"]): x.get("name", str(x["id"])) for x in client.get("/offers")}
    with Session() as db:
        dirty = db.scalars(select(StreamDraft).where(StreamDraft.campaign_id == campaign_id, StreamDraft.dirty == True)).all()
        if dirty:
            raise HTTPException(409, "There are unpublished changes. Publish or cancel them before fetching again.")
        for stream in streams:
            sid = int(stream["id"])
            row = db.get(StreamDraft, sid)
            previous = {int(o["offer_id"]): o for o in (row.draft.get("offers", []) if row else [])}
            current = []
            for item in stream.get("offers", []):
                oid = int(item["offer_id"])
                prior = previous.pop(oid, {})
                current.append({"offer_id": oid, "name": offers_by_id.get(oid, str(oid)),
                    "share": int(item.get("share", 0)), "pinned": prior.get("pinned", False),
                    "removed": item.get("state", "active") != "active"})
            # Offers removed directly in Keitaro remain available for restoration.
            for old in previous.values():
                current.append({**old, "removed": True, "pinned": False, "share": 0})
            draft = {"offers": current, "has_offers": bool(current)}
            baseline = {"remote_offers": normalized_offers(stream), "snapshot": copy.deepcopy(draft)}
            if row is None:
                row = StreamDraft(stream_id=sid, campaign_id=campaign_id, name=stream.get("name", str(sid)),
                                  baseline=baseline, draft=draft, dirty=False)
                db.add(row)
            else:
                row.name, row.baseline, row.draft, row.dirty = stream.get("name", str(sid)), baseline, draft, False
        db.commit()
        rows = db.scalars(select(StreamDraft).where(StreamDraft.campaign_id == campaign_id)).all()
        valid_ids = {int(x["id"]) for x in streams}
        for row in rows:
            if row.stream_id not in valid_ids:
                db.delete(row)
        db.commit()
        return [serialise(row) for row in rows if row.stream_id in valid_ids]


@app.get("/api/campaigns/{campaign_id}/drafts")
def get_drafts(campaign_id: int):
    with Session() as db:
        return [serialise(x) for x in db.scalars(select(StreamDraft).where(StreamDraft.campaign_id == campaign_id)).all()]


def edit(stream_id, action):
    with Session() as db:
        row = row_or_404(db, stream_id)
        new = copy.deepcopy(row.draft)
        try:
            action(new["offers"])
            rebalance(new["offers"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        new["has_offers"] = bool(new["offers"])
        row.draft, row.dirty = new, True
        db.commit()
        return serialise(row)


@app.post("/api/streams/{stream_id}/offers")
def add_offer(stream_id: int, data: AddOffer):
    def action(items):
        if any(o["offer_id"] == data.offer_id for o in items):
            raise ValueError("This offer already exists. Restore it if it is archived.")
        items.append({"offer_id": data.offer_id, "name": data.name or str(data.offer_id),
                      "share": 0, "pinned": False, "removed": False})
    return edit(stream_id, action)


@app.post("/api/streams/{stream_id}/offers/{offer_id}/{action}")
def toggle_offer(stream_id: int, offer_id: int, action: str):
    if action not in {"remove", "restore"}:
        raise HTTPException(404, "Unknown action")
    def change(items):
        offer = next((x for x in items if x["offer_id"] == offer_id), None)
        if not offer:
            raise ValueError("Offer not found.")
        offer["removed"] = action == "remove"
        if offer["removed"]:
            offer["pinned"] = False
    return edit(stream_id, change)


@app.post("/api/streams/{stream_id}/offers/{offer_id}/pin")
def pin_offer(stream_id: int, offer_id: int, data: PinOffer):
    def change(items):
        offer = next((x for x in items if x["offer_id"] == offer_id), None)
        if not offer or offer.get("removed"):
            raise ValueError("Active offer not found.")
        offer["pinned"] = data.pinned
        if data.pinned and data.share is not None:
            offer["share"] = data.share
    return edit(stream_id, change)


@app.post("/api/streams/{stream_id}/cancel")
def cancel(stream_id: int):
    with Session() as db:
        row = row_or_404(db, stream_id)
        row.draft = copy.deepcopy(row.baseline["snapshot"])
        row.dirty = False
        db.commit()
        return serialise(row)


@app.post("/api/streams/{stream_id}/publish")
def publish(stream_id: int):
    client = api()
    with Session() as db:
        row = row_or_404(db, stream_id)
        if not row.dirty:
            return serialise(row)
        active = [x for x in row.draft["offers"] if not x.get("removed")]
        if not active:
            raise HTTPException(422, "At least one active offer is required to publish.")
        campaign = client.get(f"/campaigns/{row.campaign_id}")
        if not re.match(r"^test_karklin(?:s|sh)_", campaign.get("name", ""), re.IGNORECASE):
            raise HTTPException(403, "Publishing is restricted to your test_karklins_ campaigns.")
        remote = client.get(f"/streams/{stream_id}")
        if normalized_offers(remote) != [tuple(x) for x in row.baseline["remote_offers"]]:
            raise HTTPException(409, "The stream changed in Keitaro. Cancel the draft and fetch again before publishing.")
        payload = [{"offer_id": x["offer_id"], "share": int(x["share"]), "state": "active"} for x in active]
        updated = client.put(f"/streams/{stream_id}", {"offers": payload})
        row.baseline = {"remote_offers": normalized_offers(updated), "snapshot": copy.deepcopy(row.draft)}
        row.dirty = False
        db.commit()
        return serialise(row)
