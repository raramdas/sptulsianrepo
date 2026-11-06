"""Run against the synthetic localhost preview, never production accounts."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT=Path('/tmp/capital-ledger-preview');OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
 for engine,width,height,name in [(p.chromium,1440,1000,'desktop'),(p.webkit,390,844,'iphone'),(p.webkit,375,667,'iphone-small')]:
  browser=engine.launch()
  context=browser.new_context(viewport=dict(width=width,height=height),is_mobile=width<800,has_touch=width<800,device_scale_factor=1)
  page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto('http://127.0.0.1:8765');page.get_by_label('Username',exact=True).fill('preview');page.get_by_label('Password',exact=True).fill('fixture-password')
  page.get_by_role('button',name='Enter your workspace').click();page.get_by_role('heading',name='Your capital, at a glance.').wait_for()
  page.screenshot(path=str(OUT/f'{name}-home.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(name,'home horizontal overflow')
  for dest in ('portfolio','ideas','activity','more','review'):
   page.evaluate('(dest)=>location.hash=dest',dest);page.locator('#content h1').wait_for();page.wait_for_timeout(100)
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(name,dest,'horizontal overflow')
   page.screenshot(path=str(OUT/f'{name}-{dest}.png'),full_page=True)
  page.evaluate("location.hash='portfolio'");page.get_by_label('Search holdings').fill('BSE')
  assert page.locator('tbody tr').count()==1
  page.locator('[data-holding="BSE"]').click();page.get_by_role('heading',name='BSE',exact=True).wait_for();page.get_by_role('button',name='Close details').click()
  page.get_by_label('Filter account').select_option('OLD');page.get_by_role('heading',name='No holdings to show').wait_for()
  page.get_by_label('Filter account').select_option('All accounts')
  page.evaluate("location.hash='ideas'");page.get_by_label('Search ideas').fill('CG Power');page.locator('.idea-card').click()
  page.get_by_role('button',name='Edit target').click();page.get_by_label('Target price (₹)').fill('1234');page.get_by_role('button',name='Save target',exact=True).click();page.locator('dialog').wait_for(state='hidden')
  page.reload();page.locator('#content h1').wait_for();assert page.get_by_label('Password',exact=True).count()==0
  page.evaluate("location.hash='more'");page.get_by_role('button',name='Add to Home Screen').click();page.get_by_role('heading',name='A home on your iPhone').wait_for();page.get_by_role('button',name='Close details').click()
  page.get_by_role('button',name='Sign out of this device').click();page.get_by_label('Username',exact=True).wait_for();page.reload();page.get_by_label('Username',exact=True).wait_for()
  assert not errors,(name,errors)
  print(name,'PASS: navigation, no overflow, search, filters, details, target edit, reload sign-in, install, sign-out')
  browser.close()
