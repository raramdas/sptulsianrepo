"""Offline/empty/stale/XSS/persistence checks against the synthetic server."""
import json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 browser=p.chromium.launch();context=browser.new_context(viewport={'width':390,'height':844})
 page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8765');page.get_by_label('Username',exact=True).fill('preview');page.get_by_label('Password',exact=True).fill('fixture-password');page.get_by_role('button',name='Enter your workspace').click();page.locator('#content h1').wait_for()
 saved=context.storage_state();payload=context.request.get('http://127.0.0.1:8765/api/dashboard').json()
 next_context=browser.new_context(storage_state=saved);other=next_context.new_page();other.goto('http://127.0.0.1:8765');other.locator('#content h1').wait_for();assert other.get_by_label('Password',exact=True).count()==0;next_context.close()
 bad=dict(payload);bad['holdings']=[];bad['ideas']=[];bad['gtts']=[];bad['trades']=[]
 page.route('**/api/dashboard',lambda route:route.fulfill(json=bad));page.reload();page.get_by_role('heading',name='No holdings to show').wait_for();assert page.locator('.hero-value').inner_text()=='₹0'
 bad['sync']=[];page.reload();page.get_by_role('heading',name='No holdings to show').wait_for();assert page.locator('.hero-value').inner_text()=='—';assert page.locator('.sync-strip.stale').count()==1
 page.unroute('**/api/dashboard');page.reload();page.locator('#content h1').wait_for()
 context.set_offline(True);page.get_by_role('heading',name='We couldn’t load your portfolio').wait_for();assert page.locator('.hero-value').count()==0
 context.set_offline(False);page.get_by_role('heading',name='Your capital, at a glance.').wait_for()
 hostile=dict(payload);hostile['ideas']=[dict(payload['ideas'][0],stock_name='<img src=x onerror=alert(1)>')]
 page.route('**/api/dashboard',lambda route:route.fulfill(json=hostile));page.goto('http://127.0.0.1:8765/#ideas');page.reload();page.locator('.idea-card').wait_for();assert page.locator('.idea-card img').count()==0;assert '<img' in page.locator('.idea-card h3').inner_text()
 keys=page.evaluate('Object.keys(localStorage)');assert not keys,keys
 caches=page.evaluate('async()=>{let out=[];for(const k of await window.caches.keys()){for(const r of await (await window.caches.open(k)).keys())out.push(r.url)}return out}')
 assert not any('/api/' in url for url in caches),caches
 assert not errors,errors
 browser.close();print('PASS: remembered browser context, empty vs missing data, offline privacy, XSS escaping, no API/browser data cache')
