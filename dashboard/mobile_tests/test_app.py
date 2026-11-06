import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
os.environ['DASH_STATE_DIR']=tempfile.mkdtemp(prefix='capital-import-')
from fastapi.testclient import TestClient
from mobile_app import create_app, OracleStore, Target, Budget, clean
from fixture import FixtureStore


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FixtureStore()
        self.users={'preview':'fixture-password'}
        self.app=create_app(self.store,self.users,'s'*32,self.tmp.name)
        self.client=TestClient(self.app,base_url='https://testserver')
    def login(self,remember=True):
        return self.client.post('/api/login',json=dict(username='preview',password='fixture-password',remember=remember))
    def test_auth_required_for_all_data(self):
        self.assertEqual(self.client.get('/api/dashboard').status_code,401)
        self.assertEqual(self.client.post('/api/refresh').status_code,401)
        self.assertEqual(self.client.get('/').status_code,200)
    def test_remember_cookie_and_restart(self):
        r=self.login();self.assertEqual(r.status_code,200)
        header=r.headers['set-cookie']
        for expected in ['HttpOnly','Secure','SameSite=strict','Max-Age=2592000']:self.assertIn(expected,header)
        self.assertNotIn('fixture-password',header)
        second=TestClient(create_app(self.store,self.users,'s'*32,self.tmp.name),base_url='https://testserver')
        second.cookies.update(self.client.cookies)
        self.assertEqual(second.get('/api/session').status_code,200)
    def test_session_cookie_when_not_remembering(self):
        self.assertNotIn('Max-Age',self.login(False).headers['set-cookie'])
    def test_csrf_and_logout_revocation(self):
        csrf=self.login().json()['csrf'];saved=self.client.cookies.get('capital_session')
        self.assertEqual(self.client.post('/api/refresh').status_code,403)
        self.assertEqual(self.client.post('/api/refresh',headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(self.client.post('/api/logout',headers={'X-CSRF-Token':csrf}).status_code,200)
        self.client.cookies.set('capital_session',saved)
        self.assertEqual(self.client.get('/api/session').status_code,401)
    def test_cross_site_rejected(self):
        self.assertEqual(self.client.post('/api/login',json=dict(username='preview',password='fixture-password'),headers={'sec-fetch-site':'cross-site'}).status_code,403)
        csrf=self.login().json()['csrf']
        self.assertEqual(self.client.post('/api/refresh',headers={'X-CSRF-Token':csrf,'sec-fetch-site':'cross-site'}).status_code,403)
    def test_rate_limit_persists(self):
        for _ in range(5):self.assertEqual(self.client.post('/api/login',json=dict(username='preview',password='wrong')).status_code,401)
        second=TestClient(create_app(self.store,self.users,'s'*32,self.tmp.name),base_url='https://testserver')
        self.assertEqual(second.post('/api/login',json=dict(username='preview',password='fixture-password')).status_code,429)
    def test_password_rotation_revokes_sessions(self):
        self.login();self.users['preview']='new-password'
        self.assertEqual(self.client.get('/api/session').status_code,401)
    def test_reads_never_mutate_and_do_not_cache(self):
        self.login();r=self.client.get('/api/dashboard')
        self.assertEqual(r.status_code,200);self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertEqual(r.json()['policy']['model'],'lite');self.assertEqual(self.store.writes,[])
    def test_write_validation(self):
        csrf=self.login().json()['csrf'];headers={'X-CSRF-Token':csrf}
        for amount in (0,-1,1e15):self.assertEqual(self.client.put('/api/budget',json=dict(amount=amount,expected_amount=1),headers=headers).status_code,422)
        self.assertEqual(self.client.put('/api/trades/1/target',json=dict(target_price=100,have_interest='Guess',timeframe=''),headers=headers).status_code,422)
        self.assertEqual(self.store.writes,[])
        self.assertEqual(self.client.put('/api/budget',json=dict(amount=10000,expected_amount=1),headers=headers).status_code,200)
        self.assertEqual(len(self.store.writes),1)
    def test_no_order_placement_routes(self):
        self.login()
        for route in ('buy','sell','cancel','orders','gtt'):
            self.assertEqual(self.client.post('/api/'+route,json={}).status_code,404)
    def test_unsafe_configuration_fails_closed(self):
        c=TestClient(create_app(self.store,{'admin':'changeme'},'bad',self.tmp.name),base_url='https://testserver')
        self.assertEqual(c.post('/api/login',json=dict(username='admin',password='changeme')).status_code,503)
    def test_target_conflict_rolls_back(self):
        store=OracleStore.__new__(OracleStore);store.db=Mock();conn=store.db.get_connection.return_value
        conn.cursor.return_value.fetchone.return_value=(200,'Have Interest','','Open')
        with self.assertRaises(Exception):store.target(1,Target(target_price=300,have_interest='Have Interest',timeframe='',expected_target=100,expected_interest='Have Interest'))
        conn.commit.assert_not_called();conn.rollback.assert_called_once()
    def test_ambiguous_budget_rolls_back(self):
        store=OracleStore.__new__(OracleStore);store.db=Mock();conn=store.db.get_connection.return_value
        conn.cursor.return_value.rowcount=2
        with self.assertRaises(Exception):store.budget(Budget(amount=100,expected_amount=50))
        conn.commit.assert_not_called();conn.rollback.assert_called_once()
    def test_symbol_preview_is_read_only_and_confirm_is_bound(self):
        csrf=self.login().json()['csrf'];headers={'X-CSRF-Token':csrf}
        r=self.client.post('/api/trades/99/symbol-preview',json={'symbol':'VOLTAMP'},headers=headers)
        self.assertEqual(r.status_code,200);self.assertFalse(self.store.writes)
        token=r.json()['preview_token']
        self.assertEqual(self.client.post('/api/trades/98/symbol-confirm',json={'preview_token':token},headers=headers).status_code,409)
        self.assertEqual(self.client.post('/api/trades/99/symbol-confirm',json={'preview_token':token+'x'},headers=headers).status_code,409)
        self.assertEqual(self.client.post('/api/trades/99/symbol-confirm',json={'preview_token':token}).status_code,403)
        self.assertFalse(self.store.writes)
        self.assertEqual(self.client.post('/api/trades/99/symbol-confirm',json={'preview_token':token},headers=headers).status_code,200)
        self.assertEqual(self.store.writes[0][0],'symbol')
    def test_other_sessions_cannot_confirm_symbol_preview(self):
        csrf=self.login().json()['csrf'];r=self.client.post('/api/trades/99/symbol-preview',json={'symbol':'VOLTAMP'},headers={'X-CSRF-Token':csrf})
        token=r.json()['preview_token'];new_csrf=self.login().json()['csrf']
        self.assertEqual(self.client.post('/api/trades/99/symbol-confirm',json={'preview_token':token},headers={'X-CSRF-Token':new_csrf}).status_code,409)
        self.assertFalse(self.store.writes)
    def test_restored_read_workflows(self):
        csrf=self.login().json()['csrf']
        self.assertEqual(self.client.get('/api/classification?q=VOLT').status_code,200)
        self.assertEqual(self.client.get('/api/trades/1/evidence').status_code,200)
        self.assertEqual(self.client.post('/api/reconciliation',headers={'X-CSRF-Token':csrf},json={}).status_code,200)
        self.assertFalse(self.store.writes)

    def test_static_security_and_manifest(self):
        r=self.client.get('/');self.assertIn("frame-ancestors 'none'",r.headers['content-security-policy'])
        manifest=self.client.get('/assets/manifest.webmanifest').json()
        self.assertEqual(manifest['display'],'standalone')
        self.assertEqual(self.client.get('/sw.js').headers['service-worker-allowed'],'/')
        worker=self.client.get('/sw.js').text
        self.assertNotIn("'/api/",worker)
    def test_json_missing_data(self):
        self.assertIsNone(clean(float('nan')))

if __name__=='__main__':unittest.main()
