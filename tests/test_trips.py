"""Exercise actual coordinator methods with isolated HA services and storage."""
import ast
import asyncio
import hashlib
import secrets
from copy import deepcopy
from textwrap import fill
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

ROOT=Path(__file__).parents[1]/'custom_components/household_shopping'
spec=importlib.util.spec_from_file_location('model',ROOT/'model.py')
model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)
class Error(Exception):pass
class Memory:
    def __init__(self,*a):self.saved=None
    async def async_save(self,data):self.saved=deepcopy(data)
    async def async_load(self):return deepcopy(self.saved)
class Date:
    utcnow=staticmethod(lambda:datetime.now(timezone.utc))
    now=utcnow
    parse_datetime=staticmethod(datetime.fromisoformat)
    as_local=staticmethod(lambda v:v)
    parse_date=staticmethod(lambda v:datetime.fromisoformat(v).date())
namespace=dict(vars(model),asyncio=asyncio,deepcopy=deepcopy,fill=fill,timedelta=timedelta,uuid=uuid,
    hashlib=hashlib,secrets=secrets,dt_util=Date,Store=Memory,DOMAIN='household_shopping',LOGGER=logging.getLogger('tests'),
    callback=lambda f:f,Context=lambda:SimpleNamespace(user_id=None),ServiceValidationError=Error,HomeAssistantError=Error)
tree=ast.parse((ROOT/'__init__.py').read_text())
node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Trips')
exec(compile(ast.Module(body=[node],type_ignores=[]),'coordinator','exec'),namespace)
Trips=namespace['Trips']

class TripTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        people=[SimpleNamespace(name='Alex',attributes={'user_id':'alex'}),SimpleNamespace(name='Sam',attributes={'user_id':'sam'})]
        hass=SimpleNamespace(states=SimpleNamespace(get=lambda _:None,async_all=lambda _:people))
        self.manager=Trips(hass,SimpleNamespace(data={'shopping_list':'todo.shopping_list','printer_entry':'epson'}))
        self.manager.schedule=lambda:None
        self.messages=[]
        async def message(*args,**kw):self.messages.append(args)
        self.manager.message=message
        self.rows=[{'uid':'a','summary':'[Albert Heijn] 2x Milk (Sam)','status':'needs_action'}, {'uid':'b','summary':'[Action] 1x Tape (Alex)','status':'needs_action'}]
        self.printed=[]
        self.labels=[]
        self.fail_shop=None
        async def service(domain,action,data,context=None,response=False):
            if domain=='todo' and action=='get_items':return {'todo.shopping_list':{'items':deepcopy(self.rows)}}
            if domain=='todo' and action=='update_item':
                next(i for i in self.rows if i['uid']==data['item'])['status']=data['status']
            elif domain=='brother_label_printer':self.labels.append(deepcopy(data))
            elif domain=='epson_receipt_printer':
                if data['blocks'][0]['text']==self.fail_shop:raise Error('network failure')
                self.printed.append(deepcopy(data['blocks']))
        self.manager.service=service
        self.context=SimpleNamespace(user_id='alex')
    async def trip(self):
        await self.manager.start('Albert Heijn','Alex')
        trip=self.manager.data['trips'][0]
        deadline=trip['deadline']
        await self.manager.start('Action','Alex')
        await self.manager.start('Action','Alex')
        self.assertEqual(trip['deadline'],deadline)
        self.assertEqual(trip['stores'],['Albert Heijn','Action'])
        self.assertEqual(len(self.messages),2)
        return trip
    async def test_two_shops_immutable_reprint_and_whole_trip_rollback(self):
        t=await self.trip();await self.manager.finalize(t,self.context)
        self.assertEqual(len(self.printed),2)
        self.assertEqual([i['status'] for i in self.rows],['completed','completed'])
        self.rows.append({'uid':'new','summary':'[Action] 4x Bags (Alex)','status':'needs_action'})
        before=deepcopy(self.rows)
        await self.manager.reprint(t,'',self.context)
        self.assertEqual(before,self.rows)
        self.assertEqual(self.printed[:2],self.printed[2:])
        await self.manager.rollback(t,'',self.context)
        self.assertTrue(all(i['status']=='needs_action' for i in self.rows))
        self.assertEqual(t['status'],'cancelled')
        await self.manager.rollback(t,'',self.context)
        self.assertEqual(len(self.rows),3)
    async def test_second_printer_failure_keeps_both_snapshots_and_can_undo(self):
        t=await self.trip();self.fail_shop='Action';await self.manager.finalize(t,self.context)
        self.assertEqual(t['status'],'attention')
        self.assertEqual([j['status'] for j in t['jobs']],['sent','uncertain'])
        self.assertEqual([i['status'] for i in self.rows],['completed','needs_action'])
        await self.manager.rollback(t,'',self.context)
        self.assertTrue(all(i['status']=='needs_action' for i in self.rows))
    async def test_edited_items_are_not_overwritten_by_rollback(self):
        t=await self.trip();await self.manager.finalize(t,self.context)
        self.rows[0]['summary']='[Albert Heijn] 9x Milk (Sam)'
        with self.assertRaises(ValueError):await self.manager.rollback(t,'',self.context)
        self.assertEqual(self.rows[0]['summary'],'[Albert Heijn] 9x Milk (Sam)')
    async def test_finished_trip_has_repayments_and_blocks_rollback(self):
        t=await self.trip();await self.manager.finalize(t,self.context)
        await self.manager.perform('finish',{'trip_id':t['id']},'Alex',self.context)
        await self.manager.perform('payback',{'trip_id':t['id'],'requester':'Sam','amount':'12,34','paid':True},'Sam',self.context)
        self.assertEqual(t['payback']['Sam'],{'paid':True,'amount_cents':1234})
        with self.assertRaises(ValueError):await self.manager.rollback(t,'',self.context)
    async def test_restart_during_send_requires_manual_decision(self):
        t=await self.trip();t['status']='printing';t['jobs']=[{'status':'sending'}]
        await self.manager.save();await self.manager.load()
        t=self.manager.data['trips'][0]
        self.assertEqual(t['status'],'attention');self.assertEqual(t['jobs'][0]['status'],'uncertain')
        self.assertEqual(self.printed,[])
    async def test_telegram_pairing_is_single_use_and_does_not_accept_unknown_sender(self):
        result=await self.manager.request({'operation':'pair_telegram'},self.context)
        self.assertNotIn(result['code'],str(self.manager.data['pairing']))
        await self.manager.telegram({'text':'!link '+result['code'],'user_id':123},self.context)
        self.assertEqual(self.manager.person(telegram_id='123'),'Alex')
        self.assertEqual(self.manager.data['pairing'],{})
        await self.manager.telegram({'text':'!link '+result['code'],'user_id':456},self.context)
        with self.assertRaises(Error):self.manager.person(telegram_id='456')

    async def test_opening_date_label_contains_only_date(self):
        self.manager.entry.data['label_entry']='ql'
        await self.manager.food_label({'date':'2026-11-04'},'Alex',self.context)
        self.assertEqual(self.labels,[{'config_entry_id':'ql','text':'04-11-2026','alignment':'center','text_size':'large','bold':True,'copies':1}])
        self.assertEqual(self.rows[0]['status'],'needs_action')
        await self.manager.food_label({},'Alex',self.context)
        self.assertEqual(self.labels[-1]['text'],Date.now().strftime('%d-%m-%Y'))

    async def test_custom_store_guest_trip_and_archived_receipt(self):
        await self.manager.perform('save_store', {'store': 'Local Market', 'loyalty': 'qr:123'}, 'Alex', self.context)
        self.rows.append({'uid': 'market', 'summary': '[Local Market] 1x Apples (Guest)', 'status': 'needs_action'})
        await self.manager.perform('start', {'store': 'local market', 'guest_shopper': 'Guest'}, 'Alex', self.context)
        trip = self.manager.data['trips'][-1]
        self.assertEqual((trip['owner'], trip['shopper']), ('Alex', 'Guest'))
        await self.manager.perform('print_now', {'trip_id': trip['id']}, 'Alex', self.context)
        self.assertEqual(trip['jobs'][0]['items'][0]['uid'], 'market')
        await self.manager.perform('remove_store', {'store': 'Local Market'}, 'Alex', self.context)
        self.assertNotIn('Local Market', self.manager.active_stores())
        await self.manager.perform('reprint', {'trip_id': trip['id'], 'store': 'Local Market'}, 'Alex', self.context)
        self.assertEqual(self.printed[0], self.printed[1])

    async def test_other_shopper_cannot_undo_trip(self):
        t=await self.trip()
        with self.assertRaises(ValueError):await self.manager.perform('rollback',{'trip_id':t['id']},'Sam',self.context)

    async def test_completed_trip_releases_items_for_reuse(self):
        t=await self.trip();await self.manager.finalize(t,self.context)
        await self.manager.perform('finish',{'trip_id':t['id']},'Alex',self.context)
        self.rows[0]['status']='needs_action'
        await self.manager.start('Albert Heijn','Alex')
        next_trip=self.manager.data['trips'][-1]
        await self.manager.finalize(next_trip,self.context)
        self.assertEqual([i['uid'] for i in next_trip['jobs'][0]['items']],['a'])

    async def test_frontend_cannot_override_the_logged_in_person(self):
        with self.assertRaises(Error):
            await self.manager.request({'operation':'start','user_id':'sam','store':'Action'},self.context)
    async def test_separate_shoppers_cannot_claim_same_item(self):
        t=await self.trip();await self.manager.start('Albert Heijn','Sam')
        second=self.manager.data['trips'][-1]
        await self.manager.finalize(t,self.context);await self.manager.finalize(second,self.context)
        self.assertEqual(second['jobs'][0]['items'],[])

if __name__=='__main__':unittest.main()


class PruneTests(unittest.TestCase):
    """Old settled trips are dropped; anything still owed is kept."""

    def trip(self, trip_id, status, days_old, payback=None):
        created = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        return {"id": trip_id, "status": status, "created": created, "payback": payback or {}}

    def manager(self, trips):
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None, async_all=lambda _: []))
        manager = Trips(hass, SimpleNamespace(data={}))
        manager.data["trips"] = trips
        return manager

    def test_old_settled_trips_are_removed_but_debts_and_active_trips_stay(self):
        manager = self.manager([
            self.trip("old-done", "finished", 120, {"Sam": {"paid": True, "amount_cents": 250}}),
            self.trip("old-owed", "finished", 120, {"Sam": {"paid": False, "amount_cents": 250}}),
            self.trip("old-cancelled", "cancelled", 200),
            self.trip("old-attention", "attention", 200),
            self.trip("recent", "finished", 10),
        ])
        self.assertEqual(manager.prune(), 2)
        self.assertEqual(
            [t["id"] for t in manager.data["trips"]],
            ["old-owed", "old-attention", "recent"],
        )

    def test_history_is_capped_by_dropping_the_oldest_settled_trips(self):
        trips = [self.trip(f"t{i}", "finished", 1) for i in range(Trips.MAX_TRIPS + 5)]
        trips.insert(0, self.trip("owed", "finished", 1, {"Sam": {"paid": False}}))
        manager = self.manager(trips)
        manager.prune()
        ids = [t["id"] for t in manager.data["trips"]]
        self.assertEqual(len(ids), Trips.MAX_TRIPS)
        self.assertIn("owed", ids)
        self.assertNotIn("t0", ids)


class TagTests(unittest.IsolatedAsyncioTestCase):
    """NFC tags can be linked to any store from the card."""

    def setUp(self):
        people = [SimpleNamespace(name="Alex", attributes={"user_id": "alex"})]
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None, async_all=lambda _: people))
        self.manager = Trips(hass, SimpleNamespace(data={"shopping_list": "todo.shopping_list"}))
        self.manager.schedule = lambda: None
        self.started = []

        async def start(store, actor, guest=""):
            self.started.append(store)

        self.manager.start = start
        self.context = SimpleNamespace(user_id="alex")

    async def scan(self, tag):
        await self.manager.request({"operation": "scan", "tag_id": tag, "user_id": ""}, self.context)

    async def test_default_tags_still_work(self):
        await self.scan("AH-Tag")
        self.assertEqual(self.started, ["Albert Heijn"])

    async def test_unknown_tags_are_ignored(self):
        await self.scan("front-door")
        self.assertEqual(self.started, [])

    async def test_a_saved_tag_starts_its_store_and_moves_from_the_old_one(self):
        await self.manager.request({"operation": "save_store", "store": "Lidl", "loyalty": "", "tag_id": "04:A1:B2"}, self.context)
        await self.scan("04:A1:B2")
        await self.manager.request({"operation": "save_store", "store": "Vomar", "loyalty": "", "tag_id": "AH-Tag"}, self.context)
        await self.scan("AH-Tag")
        self.assertEqual(self.started, ["Lidl", "Vomar"])
        tags = self.manager.store_tags()
        self.assertEqual(tags["Vomar"], "AH-Tag")
        self.assertEqual(tags["Albert Heijn"], "")
        self.assertEqual(tags["Lidl"], "04:A1:B2")

    async def test_cleared_tag_stops_the_default(self):
        await self.manager.request({"operation": "save_store", "store": "Action", "loyalty": "123", "tag_id": ""}, self.context)
        await self.scan("AC-Tag")
        self.assertEqual(self.started, [])

    async def test_reserved_tags_cannot_be_linked(self):
        with self.assertRaises(Error):
            await self.manager.request({"operation": "save_store", "store": "Hema", "loyalty": "", "tag_id": "reprint-tag"}, self.context)
