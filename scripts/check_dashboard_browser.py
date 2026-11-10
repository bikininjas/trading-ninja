"""Contrôle navigateur hors ligne du dashboard (Playwright + Chromium).

python scripts/check_dashboard_browser.py [répertoire_des_captures]
Toutes les réponses HTTP sont factices ; aucun compte ni serveur de trading.
"""
import asyncio,json,sys,shutil
from datetime import datetime,timezone,timedelta
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]/'dashboard/public'
OUT=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/tn-dashboard-check')
OUT.mkdir(parents=True,exist_ok=True)
STATUS={'mode':'PAPER','state':'IDLE','equity':'11.37','free_quote':'9.62','active_equity':'9.62',
        'reserve_quote':'.60','growth_quote':'1.15','drawdowns':{'from_hwm':'0'},'position':None,
        'capital_steps':{'promotions':1,'step_quote':'.25','growth_trigger':'1','interval_days':7,'next_review_at':'2026-11-16T00:00:00Z'},
        'recent':{'signals':[],'evaluations':[],'orders':[]}}
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1440,'height':1000})
  errors=[]
  controls={'enabled':True,'revision':0}
  sent=[]
  cycle_id='00000000-0000-0000-0000-000000000001'
  protection={'latest':None,'incidents':[],'freshness':'UNKNOWN','status':'UNKNOWN','reason':'OBSERVER_MISSING'}
  overview_down=False
  async def dialog(d):
   await d.accept('123456' if d.type=='prompt' else None)
  page.on('dialog',dialog)
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('console',lambda m:print('CONSOLE',m.type,m.text) if m.type=='error' else None)
  async def route(r):
   path=r.request.url.split('tn.test',1)[1].split('?',1)[0]
   if path=='/api/insights/overview' and overview_down:
    return await r.fulfill(status=503,content_type='application/json',body='{"detail":"service indisponible"}')
   if path=='/events': return await r.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps(STATUS)+'\n\n')
   if path=='/api/status': payload=STATUS
   elif path.startswith('/api/history'): payload={'rows':[]}
   elif path=='/api/settings':payload={'profile':'progressif','max_per_trade_pct':'.50','max_daily_loss_pct':'.02',
     'max_orders_per_hour':2,'confidence_floor':'.60','risk_per_trade_pct':'.005','halt_auto_resume_hours':24,
     'entry_rule_enabled':True,'application':{'revision':0,'applied_revision':0,'apply_error':None},
     'profiles':[{'key':'progressif','label':'Progressif','max_per_trade_pct':'.50','max_daily_loss_pct':'.02','max_orders_per_hour':2,'confidence_floor':'.60'}]}
   elif path=='/api/settings/preview':payload={'changes':{'min_confidence':{'scope':'GLOBAL_SAFETY'}},'positions':[]}
   elif path=='/api/control/entries':
    body=r.request.post_data_json;sent.append((path,body));controls.update(enabled=body['enabled'],revision=controls['revision']+1);payload=controls
   elif path=='/api/insights/overview':payload={'entries':controls,'opportunities':{'unique_opportunities':4,'terminal_attempts':5,
     'terminal_reasons':{'NO_SETUP':3,'NOT_SELECTED':1,'FILLED':1},'truncated':False},
     'cycles':[{'id':cycle_id,'started_at':datetime.now(timezone.utc).isoformat(),'status':'COMPLETE'}],
     'experiments':[],'protection':protection,
     'settings':{'revision':0,'applied_revision':0,'apply_error':None}}
   elif path=='/api/insights/policy':payload={'next_position':{'id':'a'*64},'legacy_adoption':{'id':'b'*64},'positions':[]}
   elif path.endswith('/events') and path.startswith('/api/insights/cycles/'):
    payload={'events':[{'stage':'APPROVED','symbol':'BTC/EUR','body':{}},{'stage':'RANKING','body':{'mode':'observe','candidates':[]}}]}
   elif path.endswith('/replay') or path=='/api/insights/replay':payload={'status':'IDENTICAL','calls':12}
   elif path.endswith('/diagnostics') and path.startswith('/api/positions/'):
    payload={'symbol':'BTC/EUR','coverage':'.5','mfe_quote':'.30','mae_quote':'-.40','realized_net_pnl_quote':'.12','fees_quote':'.05','series':[]}
   elif path=='/api/insights/confidence':payload={'cohorts':[],'selection_bias':True}

   elif path.startswith('/api/'):payload={}
   else:
    file=ROOT/('index.html' if path=='/' else path.lstrip('/'))
    if file.is_file():
     return await r.fulfill(status=200,body=file.read_bytes(),content_type={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'}.get(file.suffix,'text/plain'))
    return await r.fulfill(status=404,body='')
   await r.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
  await page.route('http://tn.test/**',route)
  await page.goto('http://tn.test/')
  await page.wait_for_function("document.querySelector('#growth-pot').textContent.includes('1,15')",timeout=7000)
  for selector,expected in [('#active-capital','9,62'),('#reserve','0,60'),('#growth-pot','1,15')]:
   assert expected in await page.locator(selector).inner_text()
  await page.wait_for_function("document.querySelector('#entries-toggle').disabled === false")
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance inconnue')")
  protection.update(status='OK',reason='OBSERVER_CURRENT',valid_until=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance à jour')")
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('preuve de surveillance expirée')")
  protection['valid_until']=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance à jour')")
  overview_down=True
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('actualisation impossible')")
  overview_down=False
  await page.locator('#entries-toggle').click()
  await page.wait_for_function("document.querySelector('#entries-status').textContent.includes('suspendus')")
  assert sent[-1][1]['enabled'] is False and sent[-1][1]['expected_revision']==0
  await page.locator('#entries-toggle').click()
  await page.wait_for_function("document.querySelector('#entries-status').textContent.includes('autorisés')")
  assert sent[-1][1]['totp']=='123456'
  await page.locator('#insights-panel summary').filter(has_text='Derniers cycles').click()
  await page.get_by_role('button',name='Vérifier le rejeu').click()
  await page.get_by_text('Décisions et rapport reproduits à l’identique.').wait_for()
  await page.locator('#insights-panel summary').filter(has_text='Comprendre une position').click()
  await page.locator('#diagnostic-id').fill(cycle_id)
  await page.locator('#diagnostic-form button').click()
  await page.wait_for_function("document.querySelector('#trade-diagnostics').textContent.includes('Meilleur PnL observé')")
  assert not any(path.endswith('/panic') for path,_ in sent)
  assert not errors,errors
  await page.screenshot(path=str(OUT/'capital-desktop.png'),full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  await page.screenshot(path=str(OUT/'capital-mobile.png'),full_page=True)
  assert not errors,errors
  print('Dashboard : expiration/panne de surveillance sans faux vert, capital, suspension/réautorisation avec TOTP, rejeu, diagnostics et mobile sans débordement.')
  await browser.close()
asyncio.run(main())
