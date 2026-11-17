import copy
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import make_engine, migrate
from app.weights import rebalance


class FakeKeitaro:
    def __init__(self):
        self.remote = {10: {"id": 10, "name": "Country", "offers": []},
                       11: {"id": 11, "name": "Offers", "offers": [
                           {"offer_id": 1, "share": 50, "state": "active"},
                           {"offer_id": 2, "share": 50, "state": "active"}]}}
        self.writes = []

    def get(self, path):
        if path == "/campaigns/1": return {"id": 1, "name": "test_karklins_demo"}
        if path == "/campaigns/1/streams": return copy.deepcopy(list(self.remote.values()))
        if path == "/offers": return [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}, {"id": 3, "name": "Three"}]
        if path.startswith("/streams/"): return copy.deepcopy(self.remote[int(path.rsplit('/', 1)[1])])
        raise AssertionError(path)

    def put(self, path, data):
        self.writes.append((path, data))
        stream_id = int(path.rsplit('/', 1)[1])
        self.remote[stream_id]["offers"] = copy.deepcopy(data["offers"])
        return copy.deepcopy(self.remote[stream_id])

    def post(self, path, data):
        self.writes.append((path, copy.deepcopy(data)))
        if path == '/campaigns': return {'id': 23, **data}
        if path == '/streams': return {'id': 99, **data}
        raise AssertionError(path)


class WeightsTest(unittest.TestCase):
    def test_pinned_and_remainder(self):
        items = [{"share": 25, "pinned": True}, {"share": 0}, {"share": 0}]
        self.assertEqual([x["share"] for x in rebalance(items)], [25, 38, 37])

    def test_removed_offer_has_no_share(self):
        items = [{"share": 30, "removed": True, "pinned": True}, {"share": 70}]
        self.assertEqual([x["share"] for x in rebalance(items)], [0, 100])
        self.assertFalse(items[0]["pinned"])

    def test_invalid_pins_are_rejected(self):
        with self.assertRaises(ValueError):
            rebalance([{"share": 60, "pinned": True}, {"share": 50, "pinned": True}])


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        migrate(self.engine)
        self.fake = FakeKeitaro()
        self.p1 = patch.object(main, 'Session', sessionmaker(bind=self.engine, expire_on_commit=False)); self.p1.start()
        self.p2 = patch.object(main, 'api', lambda: self.fake); self.p2.start()
        self.addCleanup(self.p1.stop); self.addCleanup(self.p2.stop)
        self.addCleanup(self.engine.dispose)

    def test_draft_cancel_publish_and_restore(self):
        result = main.fetch_campaign(1)
        self.assertEqual(len(result), 2)
        self.assertFalse(result[0]['has_offers'])
        response = main.add_offer(11, main.AddOffer(offer_id=3, name='Three'))
        self.assertEqual([x['share'] for x in response['offers']], [34, 33, 33])
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(len(main.cancel(11)['offers']), 2)
        main.pin_offer(11, 1, main.PinOffer(pinned=True, share=25))
        result = main.add_offer(11, main.AddOffer(offer_id=3, name='Three'))
        self.assertEqual([x['share'] for x in result['offers']], [25, 38, 37])
        self.assertFalse(main.publish(11)['dirty'])
        self.assertEqual(len(self.fake.writes), 1)
        main.toggle_offer(11, 2, 'remove')
        main.publish(11)
        self.assertNotIn(2, [x['offer_id'] for x in self.fake.remote[11]['offers']])
        response = main.toggle_offer(11, 2, 'restore')
        self.assertFalse(response['offers'][1]['removed'])

    def test_external_changes_are_not_overwritten(self):
        main.fetch_campaign(1)
        main.add_offer(11, main.AddOffer(offer_id=3))
        self.fake.remote[11]['offers'][0]['share'] = 30
        with self.assertRaises(HTTPException) as error: main.publish(11)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.fake.writes, [])

    def test_refetch_preserves_removed_offer(self):
        main.fetch_campaign(1)
        self.fake.remote[11]['offers'] = [self.fake.remote[11]['offers'][0]]
        data = main.fetch_campaign(1)
        stream = next(x for x in data if x['stream_id'] == 11)
        archived = next(x for x in stream['offers'] if x['offer_id'] == 2)
        self.assertTrue(archived['removed'])
        self.assertEqual(archived['share'], 0)

    def test_campaign_creation_builds_two_ordered_flows(self):
        args = dict(name='test_karklins_example', country='GB', offer_id=1, domain_id=2, group_id=3, traffic_source_id=4)
        response = main.create_campaign(main.CampaignIn(**args))
        self.assertEqual(response['id'], 23)
        self.assertEqual([x[0] for x in self.fake.writes], ['/campaigns', '/streams', '/streams'])
        first, second = self.fake.writes[1][1], self.fake.writes[2][1]
        self.assertEqual(first['filters'][0]['payload'], ['GB'])
        self.assertEqual(first['action_payload'], 'https://www.google.com/')
        self.assertEqual(second['offers'][0]['offer_id'], 1)
        self.assertEqual((first['position'], second['position']), (1, 2))

    def test_http_routes_validate_and_persist_drafts(self):
        client = TestClient(main.app)
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.post('/api/campaigns/1/fetch').status_code, 200)
        result = client.post('/api/streams/11/offers', json={'offer_id': 3, 'name': 'Three'})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['dirty'])
        self.assertEqual(client.post('/api/streams/11/offers', json={'offer_id': 3}).status_code, 422)
        self.assertEqual(client.post('/api/streams/11/publish').status_code, 200)

    def test_pin_and_reopen_draft_through_http(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.post('/api/campaigns/1/fetch').status_code, 200)
            result = client.post('/api/streams/11/offers/1/pin', json={'pinned': True, 'share': 25})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()['offers'][0]['pinned'])
            self.assertEqual([offer['share'] for offer in result.json()['offers']], [25, 75])
            self.assertEqual(self.fake.writes, [])

        with TestClient(main.app) as reopened:
            with patch.object(main, 'api', side_effect=AssertionError('Draft recovery should not call Keitaro')):
                result = reopened.post('/api/campaigns/1/fetch')
            self.assertEqual(result.status_code, 200, result.text)
            draft = next(stream for stream in result.json() if stream['stream_id'] == 11)
            self.assertTrue(draft['dirty'])
            self.assertEqual([offer['share'] for offer in draft['offers']], [25, 75])
            result = reopened.post('/api/streams/11/cancel')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(result.json()['dirty'])
            self.assertEqual([offer['share'] for offer in result.json()['offers']], [50, 50])
            self.assertEqual(self.fake.writes, [])

    def test_other_campaign_cannot_be_published(self):
        main.fetch_campaign(1)
        main.add_offer(11, main.AddOffer(offer_id=3))
        old_get = self.fake.get
        self.fake.get = lambda path: {'id': 1, 'name': 'another_users_campaign'} if path == '/campaigns/1' else old_get(path)
        with self.assertRaises(HTTPException) as error: main.publish(11)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.fake.writes, [])


if __name__ == '__main__':
    unittest.main()
