"""Contrôle navigateur hors ligne du dashboard (Playwright + Chromium).

python scripts/check_dashboard_browser.py [répertoire_des_captures]
Toutes les réponses HTTP sont factices ; aucun compte ni serveur de trading.
"""
import asyncio,json,sys,shutil
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
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('console',lambda m:print('CONSOLE',m.type,m.text) if m.type=='error' else None)
  async def route(r):
   path=r.request.url.split('tn.test',1)[1].split('?',1)[0]
   if path=='/events': return await r.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps(STATUS)+'\n\n')
   if path=='/api/status': payload=STATUS
   elif path.startswith('/api/history'): payload={'rows':[]}
   elif path=='/api/settings':payload={'profile':'progressif','profiles':[]}
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
  assert not errors,errors
  await page.screenshot(path=str(OUT/'capital-desktop.png'),full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  await page.screenshot(path=str(OUT/'capital-mobile.png'),full_page=True)
  print('Dashboard : trois enveloppes exactes, desktop et mobile sans débordement horizontal.')
  await browser.close()
asyncio.run(main())
