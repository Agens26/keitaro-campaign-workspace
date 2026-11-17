"""Local-only test double, never connects to a real tracker."""
from fastapi import FastAPI

app = FastAPI()
campaigns = [{"id": 1, "name": "test_karklins_example"}]
offers = [{"id": 1, "name": "Offer One"}, {"id": 2, "name": "Offer Two"}, {"id": 3, "name": "Offer Three"}]
streams = {
    1: [{"id": 10, "name": "Country to Google", "offers": []},
        {"id": 11, "name": "Offers", "offers": [{"offer_id": 1, "share": 50, "state": "active"}, {"offer_id": 2, "share": 50, "state": "active"}]}]
}


@app.get('/admin_api/v1/campaigns')
def get_campaigns(): return campaigns


@app.get('/admin_api/v1/campaigns/{campaign_id}')
def get_campaign(campaign_id: int): return next(x for x in campaigns if x['id'] == campaign_id)


@app.get('/admin_api/v1/offers')
def get_offers(): return offers


@app.get('/admin_api/v1/domains')
def get_domains(): return [{'id': 1, 'name': 'example.test'}]


@app.get('/admin_api/v1/groups')
def get_groups(): return [{'id': 1, 'name': 'Tests', 'type': 'campaigns'}]


@app.get('/admin_api/v1/traffic_sources')
def get_sources(): return [{'id': 1, 'name': 'Example source'}]


@app.get('/admin_api/v1/campaigns/{campaign_id}/streams')
def get_streams(campaign_id: int): return streams[campaign_id]


@app.get('/admin_api/v1/streams/{stream_id}')
def get_stream(stream_id: int): return next(s for group in streams.values() for s in group if s['id'] == stream_id)


@app.put('/admin_api/v1/streams/{stream_id}')
def update_stream(stream_id: int, data: dict):
    target = get_stream(stream_id)
    target.update(data)
    return target


@app.post('/admin_api/v1/campaigns')
def new_campaign(data: dict):
    campaign = {'id': len(campaigns) + 1, **data}
    campaigns.append(campaign)
    streams[campaign['id']] = []
    return campaign


@app.post('/admin_api/v1/streams')
def new_stream(data: dict):
    group = streams[data['campaign_id']]
    item = {'id': max([s['id'] for g in streams.values() for s in g] + [10]) + 1, 'offers': [], **data}
    group.append(item)
    return item
