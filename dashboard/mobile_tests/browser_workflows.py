"""Restored workflows: exercise only the synthetic localhost preview."""
import os
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 for engine,width,height,name in ([(p.webkit,390,844,'iphone')] if os.getenv('MOBILE_QA') else [(p.chromium,1440,1000,'desktop')]):
  b=engine.launch();c=b.new_context(viewport={'width':width,'height':height},is_mobile=width<800,has_touch=width<800);page=c.new_page();errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(os.getenv('QA_URL','http://127.0.0.1:8767')+'');page.get_by_label('Username',exact=True).fill('preview');page.get_by_label('Password',exact=True).fill('fixture-password');page.get_by_role('button',name='Enter your workspace').click();page.locator('#content h1').wait_for()
  page.goto(os.getenv('QA_URL','http://127.0.0.1:8767')+'/#review');page.get_by_role('button',name='Confirm stock symbols',exact=True).click();page.get_by_role('button',name='Check symbol',exact=True).click()
  page.get_by_label('Exact NSE trading symbol').fill('VOLTAMP');page.get_by_role('button',name='Check symbol',exact=True).click();page.get_by_role('heading',name='VOLTAMP TRANSFORMERS',exact=True).wait_for()
  assert page.get_by_role('button',name='Confirm VOLTAMP',exact=True).is_disabled()
  page.get_by_label('I have checked that this is the same company').check();page.get_by_role('button',name='Confirm VOLTAMP',exact=True).click();page.locator('dialog').wait_for(state='hidden')
  page.goto(os.getenv('QA_URL','http://127.0.0.1:8767')+'/#more');page.get_by_role('button',name='Company & classification lookup').click();page.get_by_label('Company name or trading symbol').fill('VOLT');page.get_by_role('button',name='Search classification').click();page.get_by_role('heading',name='Voltamp Transformers',exact=True).wait_for();page.get_by_role('button',name='Close details').click()
  page.goto(os.getenv('QA_URL','http://127.0.0.1:8767')+'/#ideas');page.get_by_label('Search ideas').fill('CG Power');page.locator('.idea-card').click();page.get_by_role('button',name='View score evidence & history').click();page.get_by_text('Check earnings update',exact=True).wait_for();page.get_by_role('button',name='Close details').click()
  page.goto(os.getenv('QA_URL','http://127.0.0.1:8767')+'/#reconcile');page.get_by_role('button',name='Check now',exact=True).click();page.get_by_role('heading',name='TEST',exact=True).wait_for();assert page.get_by_text('Some holdings have no GTT',exact=True).count()==1
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert not errors,errors
  print(name,'PASS: symbol preview/explicit confirm, classification, score evidence, read-only reconciliation')
  b.close()
