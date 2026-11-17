"""Contrôle navigateur hors ligne du dashboard (Playwright + Chromium).

python scripts/check_dashboard_browser.py [répertoire_des_captures]
Toutes les réponses HTTP sont factices ; aucun compte ni serveur de trading.
"""
import asyncio,json,sys,shutil,os
from datetime import datetime,timezone,timedelta
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]/'dashboard/public'
sys.path.insert(0,str(ROOT.parents[1]/'backend'))
from app.core.settings import load_settings
from app.api.settings_store import friendly_settings
PROFILE_OPTIONS=friendly_settings(load_settings(use_environment=False))['profiles']
OUT=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/tn-dashboard-check')
OUT.mkdir(parents=True,exist_ok=True)
STATUS={'mode':'PAPER','state':'IDLE','equity':'11.37','free_quote':'9.62','active_equity':'9.62',
        'reserve_quote':'.60','growth_quote':'1.15','drawdowns':{'daily':'.002','weekly':'.004','from_hwm':'.008'},'position':None,
        'consecutive_losses':0,'orders_last_hour':0,
        'capital_steps':{'promotions':1,'step_quote':'.25','growth_trigger':'1','interval_days':7,'next_review_at':'2026-11-16T00:00:00Z'},
        'recent':{'signals':[],'evaluations':[],'orders':[]}}
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or shutil.which('chromium'),args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1440,'height':1000})
  await page.emulate_media(reduced_motion='reduce')
  await page.clock.install()
  async def deadline(seconds):
   # Les expirations suivent l’horloge du navigateur, y compris après fast_forward.
   return await page.evaluate('(seconds) => new Date(Date.now() + seconds * 1000).toISOString()', seconds)
  # Flux déterministe : mêmes handlers que l’EventSource réel, erreurs injectables.
  await page.add_init_script('''window.EventSource = class extends EventTarget {
    constructor() { super(); window.testStream = this;
      setTimeout(() => { this.onopen?.(); this.onmessage?.({data: JSON.stringify(''' + json.dumps(STATUS) + ''')}); }, 10);
    }
  };''')
  await page.add_init_script('''
    const originalFetch = window.fetch.bind(window);
    window.fetch = (path, options) => {
      if (path === '/api/stats' && window.reviewStallStats) {
        window.reviewStatsSignal = options?.signal;
        return new Promise((resolve, reject) => {
          options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
        });
      }
      return originalFetch(path, options);
    };
  ''')
  errors=[]
  controls={'enabled':True,'revision':0}
  sent=[]
  cycle_id='00000000-0000-0000-0000-000000000001'
  protection={'latest':None,'incidents':[],'freshness':'UNKNOWN','status':'UNKNOWN','reason':'OBSERVER_MISSING'}
  overview_down=False
  overview_invalid=False
  history_invalid=False
  history_rows={}
  status_gate=None
  status_started=asyncio.Event()
  opportunities={'unique_opportunities':4,'terminal_attempts':5,
     'terminal_reasons':{'NO_SETUP':3,'NOT_SELECTED':1,'FILLED':1},'truncated':False}
  stats_invalid=False
  settings_invalid=False
  settings_macro=False
  macro_settings={'macro_timeframe':'4h','macro_ema_period':200,'eval_timeframe':'4h'}
  learning_invalid=False
  learning_down=False
  learning_data={'state':'NOT_STARTED','run':None,'valid_until':None,
   'simulations':{'total':0,'eligible':0,'pending':0,'excluded':0,'no_plan':0},'history':[],
   'policy':{'min_samples':20,'min_new_samples':5,'cooldown_hours':24}}
  intel_down=False
  intel_invalid=False
  intel_id='00000000-0000-4000-8000-000000000017'
  archive_id='00000000-0000-4000-8000-000000000016'
  intel_row={'id':intel_id,'observed_at':datetime.now(timezone.utc).isoformat(),
   'valid_until':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
   'status':'OK','model':'qwen3.5:9b','overall_sentiment':'BEARISH','sentiment_score':'-0.42',
   'systemic_risk_detected':True,'affected_assets':['BTC'],'catalyst_summary':'Le marché reste prudent. Aucun achat forcé.',
   'source_hashes':['a'*64,'b'*64],'source_items':[
    {'title':'Bitcoin : un marché en attente','description':'Une description publique.','source':'https://www.coindesk.com/arc/outboundfeeds/rss','published_at':datetime.now(timezone.utc).isoformat(),'url':'https://www.coindesk.com/markets/report'},
    {'title':'<img src=x onerror=alert(1)> Réseau stable','description':'Un lien non autorisé ne doit pas devenir cliquable.','source':'https://cointelegraph.com/rss','published_at':datetime.now(timezone.utc).isoformat(),'url':'javascript:alert(1)'}],
   'analysis_response':'{"overall_sentiment":"BEARISH","catalyst_summary":"Le marché reste prudent."}',
   'fear_and_greed_value':0,'fear_and_greed_at':datetime.now(timezone.utc).isoformat()}
  intel_archive={**intel_row,'id':archive_id,'observed_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
   'valid_until':(datetime.now(timezone.utc)-timedelta(hours=22)).isoformat(),'source_items':[],'analysis_response':None}
  async def dialog(d):
   await d.accept('123456' if d.type=='prompt' else None)
  page.on('dialog',dialog)
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('console',lambda m:print('CONSOLE',m.type,m.text) if m.type=='error' else None)
  async def route(r):
   path=r.request.url.split('tn.test',1)[1].split('?',1)[0]
   if path=='/api/insights/market-intel':
    if intel_down: return await r.fulfill(status=503,content_type='application/json',body='{}')
    if intel_invalid: return await r.fulfill(status=200,content_type='application/json',body='{}')
    selected=intel_archive if ('digest_id='+archive_id) in r.request.url else intel_row
    payload={'as_of':await deadline(0),'selected':selected,'active':intel_row,'latest_attempt':intel_row,'history':[intel_row,intel_archive]}
    return await r.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
   if path=='/api/status' and status_gate is not None:
    status_started.set()
    await status_gate.wait()
    return await r.fulfill(status=200,content_type='application/json',body=json.dumps(STATUS))
   if path=='/api/insights/overview' and overview_invalid:
    return await r.fulfill(status=200,content_type='application/json',body='{"entries":{"enabled":false}}')
   if path.startswith('/api/history/') and not path.endswith('/prompt'):
    if history_invalid:
     return await r.fulfill(status=200,content_type='application/json',body='{"rows":[{}]}')
    table=path.rsplit('/',1)[-1]
    if table in history_rows:
     return await r.fulfill(status=200,content_type='application/json',body=json.dumps({'rows':history_rows[table]}))
   if path=='/api/insights/overview' and overview_down:
    return await r.fulfill(status=503,content_type='application/json',body='{"detail":"service indisponible"}')
   if path=='/api/insights/learning' and learning_down:
    return await r.fulfill(status=503,content_type='application/json',body='{}')
   if path=='/api/settings' and settings_invalid:
    return await r.fulfill(status=200,content_type='application/json',body='{}')
   if path=='/api/stats' and stats_invalid:
    return await r.fulfill(status=200,content_type='application/json',body='{}')
   if path=='/api/insights/learning' and learning_invalid:
    return await r.fulfill(status=200,content_type='application/json',body='{"state":"LEARNED"}')
   if path=='/events': return await r.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps(STATUS)+'\n\n')
   if path=='/api/status': payload=STATUS
   elif path.endswith('/prompt'): payload={'prompt_payload':{'example':'fixture locale'}}
   elif path.startswith('/api/history/llm_signals'): payload={'rows':[{
    'id':cycle_id,'created_at':datetime.now(timezone.utc).isoformat(),'symbol':'BTC/EUR','action':'HOLD',
    'confidence':'.72','market_regime':'RANGING','model_name':'qwen3.5:9b','raw_response':{'content':'{"action":"HOLD","thesis":"Attendre la confirmation."}'},'prompt_payload':{'symbol':'BTC/EUR'},'thesis':'=1+1; <img src=x onerror=alert(1)> Attendre un signal plus clair.'}]}
   elif path.startswith('/api/history'): payload={'rows':[]}
   elif path=='/api/system/health':payload={'kraken':{'status':'CONNECTED'},'ollama':{'status':'UP'},'supabase':{'status':'KNOWN','quota_alert_triggered':False}}
   elif path=='/api/stats':payload={
    'pnl':{'net_pnl':'.47','wins':3,'trades':5,'losses':2,'win_rate':'.6','r_multiple':'.24','fees':'.12','payoff_ratio':'1.4','profit_factor_net':'1.3'},
    'capital':{'engaged_position_quote':'0'},
    'shadow':{'total':0,'planned':0,'no_plan':0,'open':0,'closed':0,'wins':0,'losses':0,'net_pnl':'0','by_kind':{},'by_rejection':[]},
    'lessons':{'active':[],'source_count':0,'version':None,'updated_at':None},
    'equity_curve':[{'at':(datetime.now(timezone.utc)-timedelta(hours=4*(23-i))).isoformat(),'equity':str(round(10.9+i*.02+(.04 if i%3==0 else -.02),2))} for i in range(24)],
    'daily':[{'day':(datetime.now(timezone.utc)-timedelta(days=13-i)).date().isoformat(),'net_pnl':str(.12 if i%3 else -.06),'trades':1,'wins':int(i%3!=0)} for i in range(14)],
    'llm':{'model':'qwen3.5:9b','total':8,'counts':{'HOLD':6,'BUY':2,'SELL':0,'HALT':0},'degraded':0,'last':{'action':'HOLD','confidence':'.72','inference_time_ms':1240,'thesis':'Le marché évolue sans tendance claire. Le moteur conserve les euros en attendant une opportunité conforme aux limites.'}},
    'rejections':[{'code':'SIZING_BELOW_EXCHANGE_MINIMUM','count':3,'percentage':'.75'},{'code':'CONFIDENCE_TOO_LOW','count':1,'percentage':'.25'}]}
   elif path=='/api/settings':
    if r.request.method=='POST': sent.append((path,r.request.post_data_json))
    payload={'profile':'progressif','max_per_trade_pct':'.50','max_daily_loss_pct':'.02',
     'max_orders_per_hour':2,'confidence_floor':'.60','risk_per_trade_pct':'.005','halt_auto_resume_hours':24,
     'entry_rule_enabled':True,'application':{'revision':0,'applied_revision':0,'apply_error':None},
     'max_weekly_loss_pct':'.05','max_drawdown_from_hwm_pct':'.1','max_consecutive_losses':3,
     'cost_aware':True,'profiles':PROFILE_OPTIONS}
    if r.request.method=='POST':payload['application']['apply_error']='Erreur de recette du moteur'
    if settings_macro:
     if r.request.method=='POST':
      macro_settings.update({key:r.request.post_data_json[key] for key in macro_settings})
     payload.update(macro_settings)
   elif path=='/api/settings/preview':payload={'changes':{'min_confidence':{'scope':'GLOBAL_SAFETY'}},'positions':[]}
   elif path=='/api/control/entries':
    body=r.request.post_data_json;sent.append((path,body));controls.update(enabled=body['enabled'],revision=controls['revision']+1);payload=controls
   elif path=='/api/insights/overview':payload={'entries':controls,'opportunities':opportunities,
     'cycles':[{'id':cycle_id,'started_at':datetime.now(timezone.utc).isoformat(),'status':'COMPLETE'}],
     'experiments':[],'protection':protection,
     'settings':{'revision':0,'applied_revision':0,'apply_error':None}}
   elif path=='/api/insights/learning':payload=learning_data
   elif path=='/api/insights/policy':payload={'next_position':{'id':'a'*64},'legacy_adoption':{'id':'b'*64},'positions':[]}
   elif path.endswith('/events') and path.startswith('/api/insights/cycles/'):
    payload={'events':[{'stage':'APPROVED','symbol':'BTC/EUR','body':{}},{'stage':'RANKING','body':{'mode':'observe','candidates':[]}}]}
   elif path.endswith('/replay') or path=='/api/insights/replay':payload={'status':'IDENTICAL','calls':12}
   elif path.endswith('/diagnostics') and path.startswith('/api/positions/'):
    payload={'symbol':'BTC/EUR','coverage':'.5','mfe_quote':'.30','mae_quote':'-.40','realized_net_pnl_quote':'.12','fees_quote':'.05','series':[]}
   elif path=='/api/insights/confidence':payload={'cohorts':[],'selection_bias':True}

   elif path.startswith('/api/control/'):
    sent.append((path,r.request.post_data_json if r.request.post_data else {}));payload=STATUS
   elif path.startswith('/api/'):payload={}
   else:
    file=ROOT/('index.html' if path=='/' else path.lstrip('/'))
    if file.is_file():
     return await r.fulfill(status=200,body=file.read_bytes(),content_type={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'}.get(file.suffix,'text/plain'))
    return await r.fulfill(status=404,body='')
   await r.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
  await page.route('http://tn.test/**',route)
  await page.goto('http://tn.test/')
  await page.wait_for_function("document.querySelector('#growth-pot').textContent.includes('1,15')",timeout=7000)
  for selector,expected in [('#active-capital','9,62'),('#reserve','0,60'),('#growth-pot','1,15')]:
   assert expected in await page.locator(selector).inner_text()
  await page.wait_for_function("document.querySelector('#equity-chart svg') !== null")
  assert await page.locator('[data-view]:visible').count()==1
  assert await page.locator('#view-overview').is_visible()
  await page.wait_for_function("document.querySelector('#diagnosis-lead').textContent.includes('setup')")
  assert await page.locator('#readiness-engine').inner_text()=='Non confirmée', 'Une API disponible ne prouve pas la surveillance.'
  assert await page.locator('#observed-filled').inner_text()=='1'
  # Diagnostic historique : refus de financement, absence de preuve, collecte tronquée.
  initial_opportunities=dict(opportunities)
  opportunities.update(terminal_attempts=4,terminal_reasons={'RISK_REJECTED':4},rejection_codes={'SIZING_BELOW_EXCHANGE_MINIMUM':4})
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#diagnosis-detail').textContent.includes('minima')")
  overview_invalid=True
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#diagnosis-coverage').textContent==='Lecture ancienne'")
  assert 'minima' in await page.locator('#diagnosis-detail').inner_text()
  assert 'conservée' in await page.locator('#observations-updated').inner_text()
  overview_invalid=False
  opportunities.update(unique_opportunities=0,terminal_attempts=0,terminal_reasons={},truncated=True)
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#diagnosis-coverage').textContent==='Période tronquée'")
  assert 'ne prouve' in await page.locator('#diagnosis-detail').inner_text()
  opportunities.clear();opportunities.update(initial_opportunities)
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#diagnosis-lead').textContent.includes('setup')")
  assert not sent, 'La lecture ne doit émettre aucune commande.'
  await page.screenshot(path=str(OUT/'overview-desktop.png'),full_page=True)
  await page.locator('[data-range="7d"]').click()
  await page.locator('#equity-chart svg').focus()
  await page.keyboard.press('Home')
  assert await page.locator('#equity-tooltip').is_visible()
  await page.keyboard.press('End')
  await page.locator('[data-range="all"]').click()
  assert await page.locator('#equity-tooltip').is_hidden()
  # Une réponse partielle ne doit jamais faire disparaître une position ouverte.
  opened={**STATUS,'state':'IN_POSITION','position':{'id':cycle_id,'symbol':'BTC/EUR',
    'entry_price':'100','amount':'.05','stop_loss_price':'95','take_profit_price':None,
    'opened_at':datetime.now(timezone.utc).isoformat(),'risk_amount_quote':'.25',
    'estimated_net_risk_quote':'.33','unrealized_pnl_quote':None,'unrealized_pnl_pct':None}}
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(opened)+")})")
  await page.wait_for_function("document.querySelector('#position-symbol').textContent==='BTC/EUR'")
  await page.evaluate("testStream.onmessage({data:JSON.stringify({mode:'PAPER',state:'IDLE',equity:'11.37',free_quote:'9.62',reserve_quote:'.60'})})")
  assert await page.locator('#position-symbol').inner_text()=='BTC/EUR', 'Une réponse partielle efface la position.'
  assert '0,0 %' not in await page.locator('#position').inner_text(), 'Un PnL inconnu est affiché comme zéro.'
  assert 'non' not in await page.locator('#position').inner_text(), 'Le breakeven inconnu est affiché comme inactif.'
  multiple={**opened,'positions':[opened['position'],{**opened['position'],'id':'00000000-0000-0000-0000-000000000002','symbol':'ETH/EUR','stop_loss_price':'80'}]}
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(multiple)+")})")
  assert await page.locator('#position section').count()==2, 'Une position du portefeuille est masquée.'
  assert 'ETH/EUR' in await page.locator('#runtime-position').inner_text()
  damaged={**multiple,'positions':[opened['position'],{'symbol':'ETH/EUR'}]}
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(damaged)+")})")
  assert await page.locator('#position section').count()==2, 'Une réponse partielle efface une position.'
  await page.screenshot(path=str(OUT/'multiple-positions.png'),full_page=True)
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(STATUS)+")})")
  # Les statistiques invalides conservent valeurs et courbe, avec un avertissement.
  before_pnl=await page.locator('#stat-pnl').inner_text()
  stats_invalid=True
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#refresh-all').disabled === false")
  assert await page.locator('#stat-pnl').inner_text()==before_pnl, 'Les statistiques incomplètes remplacent les valeurs valides.'
  assert await page.locator('#equity-chart svg').count()==1
  assert await page.locator('#stats-warning').is_visible()
  stats_invalid=False
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#refresh-all').disabled === false")
  assert await page.locator('#stats-warning').is_hidden()
  # Une requête sans réponse doit libérer le rafraîchissement et préserver les chiffres.
  await page.evaluate('window.reviewStallStats = true')
  await page.locator('#refresh-all').click()
  await page.wait_for_function('window.reviewStatsSignal instanceof AbortSignal')
  await page.clock.fast_forward(10100)
  await page.wait_for_function("document.querySelector('#refresh-all').disabled === false",timeout=15000)
  assert await page.locator('#stat-pnl').inner_text()==before_pnl
  assert await page.locator('#stats-warning').is_visible()
  await page.evaluate('window.reviewStallStats = false')
  await page.locator('#refresh-all').click()
  await page.wait_for_function("document.querySelector('#refresh-all').disabled === false")
  assert await page.locator('#stats-warning').is_hidden()
  # Un message invalide, puis une coupure, ne doivent effacer ni redater le statut.
  before=await page.locator('#updated-footer').inner_text()
  await page.evaluate("testStream.onmessage({data:JSON.stringify({error:'interrompu'})})")
  assert await page.locator('#equity').inner_text()=='11,37'
  assert await page.locator('#updated-footer').inner_text()==before
  assert await page.locator('#data-warning').is_visible()
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(STATUS)+")})")
  assert await page.locator('#data-warning').is_hidden()
  await page.evaluate("testStream.onerror()")
  assert await page.locator('#data-warning').is_visible()
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(STATUS)+")})")
  # Changer d’espace est une navigation, pas une mutation de trading.
  status_gate=asyncio.Event()
  await page.locator('#refresh-all').click()
  await asyncio.wait_for(status_started.wait(),5)
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps({**STATUS,'equity':'12.34'})+")})")
  status_gate.set()
  await page.wait_for_function("document.querySelector('#refresh-all').disabled === false")
  assert await page.locator('#equity').inner_text()=='12,34', 'Une ancienne réponse HTTP écrase le flux plus récent.'
  status_gate=None
  await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(STATUS)+")})")
  await page.locator('[data-nav=portfolio]').click()
  await page.locator('#view-portfolio').wait_for(state='visible')
  assert await page.locator('#growth-pot').is_visible()
  await page.screenshot(path=str(OUT/'portfolio-desktop.png'),full_page=True)
  await page.locator('[data-nav=decisions]').click()
  await page.locator('#view-decisions').wait_for(state='visible')
  await page.wait_for_function("document.querySelector('#learning-state').textContent.includes('n’a pas encore')")
  learning_data.update(state='LEARNED',valid_until=await deadline(5),
    run={'status':'LEARNED','reason':'NEW_LESSONS','started_at':datetime.now(timezone.utc).isoformat(),
         'finished_at':datetime.now(timezone.utc).isoformat(),'source_count':20,'new_source_count':20},
    history=[{'version':1,'active':True,'valid_until':None,'created_at':datetime.now(timezone.utc).isoformat(),
              'source_count':20,'model_name':'fixture','lessons':['<img src=x onerror=alert(1)> Observation fictive.']}])
  await page.locator('#learning-refresh').click()
  await page.wait_for_function("document.querySelector('#learning-state').textContent.includes('nouvelle version')")
  assert await page.locator('#learning-history img').count()==0
  memory_stamp=await page.locator('#learning-updated').inner_text()
  learning_invalid=True
  await page.locator('#learning-refresh').click()
  await page.wait_for_function("document.querySelector('#learning-refresh').disabled === false")
  assert 'impossible' in await page.locator('#learning-state').inner_text(), 'Une mémoire incomplète paraît à jour.'
  assert await page.locator('#learning-updated').inner_text()==memory_stamp
  learning_invalid=False
  await page.locator('#learning-refresh').click()
  await page.wait_for_function("document.querySelector('#learning-refresh').disabled === false")

  await page.clock.fast_forward(6000)
  await page.wait_for_function("document.querySelector('#learning-state').textContent.includes('récemment')")
  learning_down=True
  await page.locator('#learning-refresh').click()
  await page.wait_for_function("document.querySelector('#learning-state').textContent.includes('impossible')")
  await page.clock.fast_forward(2000)
  assert 'impossible' in await page.locator('#learning-state').inner_text()
  learning_down=False
  learning_data['valid_until']=await deadline(120)
  await page.locator('#learning-refresh').click()
  await page.wait_for_function("document.querySelector('#learning-state').textContent.includes('nouvelle version')")
  await page.wait_for_function("document.querySelector('#entries-toggle').disabled === false")
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance inconnue')")
  protection.update(status='ALERT',reason='OBSERVER_NOTIFICATION_PENDING')
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('alerte du surveillant en attente de livraison')")
  protection.update(status='OK',reason='OBSERVER_CURRENT',valid_until=await deadline(10))
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance à jour')")
  assert await page.locator('#readiness-engine').inner_text()=='Confirmée'
  await page.clock.fast_forward(11000)
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('preuve de surveillance expirée')")
  assert await page.locator('#readiness-engine').inner_text()=='Non confirmée'
  protection['valid_until']=await deadline(30)
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('Surveillance à jour')")
  overview_down=True
  await page.locator('#insights-refresh').click()
  await page.wait_for_function("document.querySelector('#protection-results').textContent.includes('actualisation impossible')")
  overview_down=False
  learning_down=False
  learning_data={'state':'NOT_STARTED','run':None,'valid_until':None,
   'simulations':{'total':0,'eligible':0,'pending':0,'excluded':0,'no_plan':0},'history':[],
   'policy':{'min_samples':20,'min_new_samples':5,'cooldown_hours':24}}
  await page.locator('#entries-toggle').click()
  await page.wait_for_function("document.querySelector('#entries-status').textContent.includes('suspendus')")
  assert sent[-1][1]['enabled'] is False and sent[-1][1]['expected_revision']==0
  await page.locator('#entries-toggle').click()
  await page.wait_for_function("document.querySelector('#entries-status').textContent.includes('autorisés')")
  assert sent[-1][1]['totp']=='123456'
  await page.locator('#insights-cycles').evaluate('(node) => { node.parentElement.open = true; }')
  await page.get_by_role('button',name='Vérifier le rejeu').click()
  await page.get_by_text('Décisions et rapport reproduits à l’identique.').wait_for()
  await page.locator('#insights-panel summary').filter(has_text='Comprendre une position').click()
  await page.locator('#diagnostic-id').fill(cycle_id)
  await page.locator('#diagnostic-form button').click()
  await page.wait_for_function("document.querySelector('#trade-diagnostics').textContent.includes('Meilleur PnL observé')")
  assert not any(path.endswith('/panic') for path,_ in sent)
  await page.locator('[data-nav=settings]').click()
  await page.locator('#view-settings').wait_for(state='visible')
  draft=await page.locator('#set-profile').input_value()
  settings_invalid=True
  await page.locator('#settings-reload').click()
  await page.wait_for_function("document.querySelector('#settings-feedback').textContent.includes('incomplets')")
  assert await page.locator('#set-profile').input_value()==draft, 'Une réponse invalide efface les réglages.'
  settings_invalid=False
  await page.locator('#set-profile').select_option('nano_budget')
  assert await page.locator('#set-notional').input_value()=='100'
  assert '100 %' in await page.locator('#set-profile-help').inner_text()
  await page.locator('#profile-example-capital').fill('5')
  assert '5,00' in await page.locator('#profile-example').inner_text()
  assert '0,25' in await page.locator('#profile-example').inner_text()
  assert await page.locator('#profile-concentration').is_visible()
  assert 'presque tout' in await page.locator('#profile-concentration').inner_text()
  # Un exemple invalide ne doit ni modifier les réglages ni empêcher leur envoi.
  await page.locator('#profile-example-capital').fill('0')
  assert 'supérieur à zéro' in await page.locator('#profile-example').inner_text()
  await page.locator('#settings-save').click()
  await page.wait_for_function("document.querySelector('#settings-save').disabled === false")
  assert await page.locator('#settings-feedback').get_attribute('data-kind')=='error'
  assert 'moteur signale une erreur' in await page.locator('#settings-feedback').inner_text()
  saved=next(body for path,body in reversed(sent) if path=='/api/settings')
  assert saved['profile']=='nano_budget' and saved['max_per_trade_pct']==1
  assert 'macro_timeframe' not in saved and await page.locator('#timeframe-settings').is_hidden(), 'L’ancienne API ne doit pas recevoir des options qu’elle ignore.'
  await page.locator('#set-profile').select_option('micro_budget')
  await page.locator('#profile-example-capital').fill('9,37')
  assert '4,69' in await page.locator('#profile-example').inner_text()
  assert '0,28' in await page.locator('#profile-example').inner_text()
  assert await page.locator('#profile-concentration').is_hidden()
  await page.locator('#set-notional').fill('25')
  await page.locator('#set-notional').dispatch_event('input')
  assert '2,34' in await page.locator('#profile-example').inner_text()
  await page.locator('#set-notional').fill('50')
  await page.locator('#set-notional').dispatch_event('input')
  assert await page.locator('#profile-comparison-body tr').count()==len(PROFILE_OPTIONS)
  await page.get_by_text('Les mots utiles, sans jargon',exact=True).click()
  assert 'ne signifie pas 80 % de chances' in await page.locator('.profile-guide').inner_text()
  await page.get_by_text('Comparer tous les profils',exact=True).click()
  assert await page.locator('#set-notional').input_value()=='50'
  assert await page.locator('#set-notional').get_attribute('max')=='50'
  settings_macro=True
  await page.locator('#settings-reload').click()
  await page.locator('#timeframe-settings').wait_for(state='visible')
  await page.locator('#set-macro-timeframe').select_option('1h')
  await page.locator('#set-macro-period').fill('75')
  await page.locator('#set-eval-timeframe').select_option('1h')
  await page.locator('#settings-save').click()
  await page.wait_for_function("document.querySelector('#settings-save').disabled === false")
  saved=next(body for path,body in reversed(sent) if path=='/api/settings')
  assert saved['macro_timeframe']=='1h' and saved['macro_ema_period']==75 and saved['eval_timeframe']=='1h'
  assert await page.locator('#set-macro-timeframe').input_value()=='1h'
  assert not errors,errors
  await page.evaluate('scrollTo(0,0)')
  await page.screenshot(path=str(OUT/'settings-desktop.png'),full_page=True)
  await page.locator('.profile-guide').screenshot(path=str(OUT/'profile-guide-desktop.png'))
  # L’inspecteur de prompt se ferme même sans toucher aux périodes du graphique.
  await page.locator('[data-nav=activity]').click()
  await page.locator('#view-activity').wait_for(state='visible')
  assert await page.locator('#activity-body img').count()==0, 'Les thèses sont du texte.'
  assert 'probabilité' in await page.locator('#activity-context-description').inner_text()
  await page.locator('#activity-search').fill('introuvable')
  assert await page.locator('#activity-export').is_disabled()
  assert await page.locator('#activity-body tr:visible').count()==0
  await page.locator('#activity-search').fill('btc')
  assert await page.locator('#activity-body tr:visible').count()==1
  assert not await page.locator('#activity-export').is_disabled()
  history_invalid=True
  await page.locator('#activity-refresh').click()
  await page.wait_for_function("!document.querySelector('#activity-warning').hidden")
  assert await page.locator('#activity-body tr:visible').count()==1, 'Une réponse invalide efface le journal.'
  assert 'conservée' in await page.locator('#activity-warning').inner_text()
  history_invalid=False
  await page.locator('#activity-refresh').click()
  await page.wait_for_function("document.querySelector('#activity-warning').hidden")
  await page.get_by_role('button',name='Voir',exact=True).click()
  await page.locator('#prompt-dialog').wait_for(state='visible')
  assert await page.locator('#prompt-dialog').is_visible()
  await page.locator('#prompt-close').click()
  assert await page.locator('#prompt-dialog').is_hidden()
  async with page.expect_download() as pending:
   await page.locator('#activity-export').click()
  download=await pending.value
  downloaded=Path(await download.path()).read_text(encoding='utf-8-sig')
  assert "'=1+1" in downloaded, 'Formules CSV neutralisées.'
  assert 'id;' in downloaded.replace('"','')
  await page.locator('#tab-orders').click()
  await page.wait_for_function("document.querySelector('#activity-export').disabled")
  now=datetime.now(timezone.utc).isoformat()
  history_rows['orders']=[{'id':cycle_id,'created_at':now,'symbol':'BTC/EUR','side':'BUY','purpose':'ENTRY','status':'UNKNOWN',
    'requested_amount':'0.0000000001','filled_amount':'0','average_fill_price':'0','fee_amount':'0','fee_currency':'EUR','position_id':cycle_id},
    {'id':'00000000-0000-0000-0000-000000000002','created_at':now,'symbol':'ETH/EUR','side':'SELL','purpose':'STOP_LOSS','status':'FILLED',
     'requested_amount':'0.1234567891','filled_amount':'0.1234567891','average_fill_price':'1234.5678901234','fee_amount':'0.0023456789','fee_currency':'EUR'}]
  await page.locator('#activity-refresh').click()
  await page.wait_for_function("document.querySelectorAll('#activity-body tr').length===2")
  assert await page.locator('#activity-body [data-attention=true]').count()==1
  assert '0,0000000001' in await page.locator('#activity-body').inner_text(), 'Une quantité du journal est arrondie.'
  assert 'Inconnus / rejetés' in await page.locator('#activity-summary').inner_text()
  await page.locator('#activity-search').fill('ETH')
  async with page.expect_download() as pending:
   await page.locator('#activity-export').click()
  content=Path(await (await pending.value).path()).read_text(encoding='utf-8-sig')
  assert '1234.5678901234' in content and '0.1234567891' in content
  assert 'ETH/EUR' in content and 'BTC/EUR' not in content, 'L’export doit suivre le filtre.'
  await page.locator('#activity-search').fill('BTC')
  await page.locator('#activity-body button').filter(has_text='Inspecter').first.click()
  assert await page.locator('#activity-detail-dialog').is_visible()
  assert '0.0000000001' in await page.locator('#activity-detail-content').inner_text()
  await page.locator('#activity-diagnose').click()
  await page.wait_for_function("!document.querySelector('#view-decisions').hidden")
  assert await page.locator('#diagnostic-id').input_value()==cycle_id
  await page.wait_for_function("document.querySelector('#trade-diagnostics').textContent.includes('Meilleur PnL observé')")
  await page.locator('[data-nav=activity]').click()
  await page.locator('#activity-search').fill('')
  history_rows['risk_evaluations']=[{'id':cycle_id,'created_at':now,'symbol':'BTC/EUR','approved':None,'rejection_code':None,'market_price':'0.0000000001','effective_risk_quote':None}]
  await page.locator('#tab-risk_evaluations').click()
  await page.wait_for_function("document.querySelector('#activity-body').textContent.includes('Inconnu')")
  assert 'Refusé' not in await page.locator('#activity-body').inner_text(), 'Un verdict manquant est inconnu.'
  assert '0,0000000001' in await page.locator('#activity-body').inner_text()
  await page.locator('#tab-llm_signals').click()
  await page.screenshot(path=str(OUT/'activity-desktop.png'),full_page=True)
  # Ouvrir ou annuler le pilotage ne doit envoyer aucun ordre.
  previous=len(sent)
  await page.locator('.page-actions [data-open-controls]').click()
  await page.locator('#panic').click()
  assert await page.locator('#panic-dialog').is_visible()
  await page.locator('#panic-cancel').click()
  assert len(sent)==previous
  await page.locator('#control-close').click()
  await page.evaluate("location.hash='settings'")
  await page.wait_for_function("!document.querySelector('#view-settings').hidden")
  assert await page.locator('#set-notional').input_value()=='50'
  # Veille causale, réponse finale, liens sûrs, archives et expiration locale.
  previous_commands=len(sent)
  intel_row['observed_at']=await deadline(-60)
  intel_row['valid_until']=await deadline(3600)
  await page.evaluate("location.hash='intel'")
  await page.wait_for_function("document.querySelector('#intel-summary').textContent.includes('marché reste prudent')")
  assert await page.locator('#intel-fng-value').inner_text()=='0 / 100'
  assert await page.locator('#intel-veto-title').inner_text()=='Veto systémique actif'
  assert await page.locator('#intel-news-list article').count()==2
  assert await page.locator('#intel-news-list img').count()==0
  assert await page.locator('#intel-news-list a').count()==1
  assert await page.locator('#intel-news-list a').get_attribute('rel')=='noopener noreferrer'
  await page.locator('#intel-search').fill('Bitcoin')
  assert await page.locator('#intel-news-list article').count()==1
  await page.locator('#intel-search').fill('')
  await page.locator('#intel-source').select_option('Cointelegraph')
  assert await page.locator('#intel-news-list article').count()==1
  await page.locator('#intel-source').select_option('')
  await page.locator('#intel-signals-list details').first.locator('summary').click()
  assert 'Attendre la confirmation' in await page.locator('#intel-signals-list pre').first.inner_text()
  await page.locator('#intel-history').select_option(archive_id)
  await page.wait_for_function("document.querySelector('#intel-freshness').textContent.includes('expirée')")
  assert 'digest='+archive_id in page.url
  assert 'n’ont pas été conservés' in await page.locator('#intel-news-list').inner_text()
  assert await page.locator('#intel-veto-title').inner_text()=='Veto systémique actif'
  await page.locator('#intel-latest').click()
  await page.wait_for_function("document.querySelectorAll('#intel-news-list article').length===2")
  intel_down=True
  await page.locator('#intel-refresh').click()
  await page.wait_for_function("!document.querySelector('#intel-error').hidden")
  assert await page.locator('#intel-news-list article').count()==2
  assert 'non confirmé' in await page.locator('#intel-veto-title').inner_text()
  intel_down=False; intel_invalid=True
  await page.locator('#intel-refresh').click()
  await page.wait_for_function("document.querySelector('#intel-error').textContent.includes('incomplète')")
  assert await page.locator('#intel-news-list article').count()==2
  intel_invalid=False
  intel_row['valid_until']=await deadline(1)
  await page.locator('#intel-refresh').click()
  await page.wait_for_function("document.querySelector('#intel-error').hidden")
  await page.clock.fast_forward(1100)
  assert 'expirée' in await page.locator('#intel-freshness').inner_text()
  assert 'Aucune analyse actuellement valide' in await page.locator('#intel-veto-title').inner_text()
  intel_row['valid_until']=await deadline(3600)
  await page.locator('#intel-refresh').click()
  await page.wait_for_function("document.querySelector('#intel-freshness').textContent==='Encore valide'")
  assert len(sent)==previous_commands, 'La veille doit rester en lecture seule.'
  for width in [320,390,768,1440]:
   await page.set_viewport_size({'width':width,'height':900})
   for view in ['overview','portfolio','decisions','activity','intel','settings']:
    await page.evaluate("testStream.onmessage({data:JSON.stringify("+json.dumps(STATUS)+")})")
    await page.evaluate('(view)=>location.hash=view',view)
    await page.wait_for_function('(view)=>!document.querySelector(`[data-view="${view}"]`).hidden',arg=view)
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Débordement {view} à {width}px'
    if width in (390,1440):
     await page.evaluate('scrollTo(0,0)')
     await page.screenshot(path=str(OUT/f'{view}-{width}.png'),full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  await page.locator('#menu-toggle').click()
  assert await page.locator('#menu-toggle').get_attribute('aria-expanded')=='true'
  await page.screenshot(path=str(OUT/'navigation-mobile.png'))
  await page.locator('[data-nav=overview]').click()
  assert await page.locator('#menu-toggle').get_attribute('aria-expanded')=='false'
  await page.locator('#menu-toggle').click()
  await page.keyboard.press('Escape')
  assert await page.locator('#menu-toggle').get_attribute('aria-expanded')=='false'
  await page.evaluate("location.hash='inconnu'")
  await page.wait_for_function("location.hash==='#overview'")
  # Deep link et formulaires de connexion (sans authentification réelle).
  await page.goto('http://tn.test/?table=orders&limit=10&q=BTC#activity')
  await page.wait_for_function("document.querySelectorAll('#activity-body tr').length===2")
  assert await page.locator('#tab-orders').get_attribute('aria-selected')=='true'
  assert await page.locator('#activity-limit').input_value()=='10'
  assert await page.locator('#activity-search').input_value()=='BTC'
  assert await page.locator('#activity-body tr:visible').count()==1
  await page.locator('#menu-toggle').click()
  await page.locator('[data-nav=portfolio]').click()
  await page.locator('#menu-toggle').click()
  await page.locator('[data-nav=activity]').click()
  assert await page.locator('#activity-search').input_value()=='BTC'
  await page.evaluate('document.fonts.ready')
  assert await page.evaluate("document.fonts.check('14px \"Plex Sans\"')"), 'La police locale doit être disponible.'
  await page.goto('http://tn.test/#settings')
  assert await page.locator('#view-settings').is_visible()
  for width in [1440,390]:
   await page.set_viewport_size({'width':width,'height':900})
   await page.goto('http://tn.test/login.html?error=1')
   assert await page.locator('#login-error').is_visible()
   assert await page.locator('input[name=totp]').get_attribute('pattern')=='[0-9]{6}'
   assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   await page.screenshot(path=str(OUT/f'login-{width}.png'),full_page=True)
  assert not errors,errors
  print('Dashboard v3 : synthèse, motifs/minima/collecte tronquée, observations invalides, surveillance expirée, journal conservé, recherche, décimales et CSV source filtré, diagnostic depuis un ordre ; 6 espaces, news/analyses causales et expiration, TOTP, rejeu, profils, connexion et 4 largeurs vérifiés.')
  await browser.close()
asyncio.run(main())
