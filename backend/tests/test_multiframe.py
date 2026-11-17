"""Commutation à chaud, sérialisation causale et contexte transmis au modèle."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.core.settings import load_settings
from app.api.settings_store import build_payload, friendly_settings
from app.api.main import SettingsUpdate
from app.runtime.orchestrator import Orchestrator
from app.execution.paper import PaperBroker
from app.insights.policy import position_policy, apply_policy
from app.insights.replay import replay_cycle
from app.llm.client import SignalClient
from tests.test_insights import config, FeatureLedger, historical_data
from app.insights.experiments import HistoricalExchange, ExperimentSignal
from tests.test_risk_gate import NOW, market
from tests.test_entry_rule import context


@pytest.mark.parametrize('tf',['1h','4h','1d'])
def test_env_aliases_and_admin_payload_configure_both_clocks(tf):
    cfg=load_settings(use_environment=False,overrides={'MACRO_TIMEFRAME':tf,'MACRO_EMA_PERIOD':'75','EVAL_TIMEFRAME':tf})
    assert cfg.risk.macro_timeframe==tf and cfg.risk.macro_ema_period==75
    assert cfg.exchange.daily_timeframe_decision==tf and cfg.mode=='paper'
    update=SettingsUpdate(macro_timeframe=tf,macro_ema_period=75,eval_timeframe=tf).model_dump(exclude_none=True)
    data=build_payload({},update)
    assert data=={'MACRO_TIMEFRAME':tf,'MACRO_EMA_PERIOD':'75','EVAL_TIMEFRAME':tf}
    assert friendly_settings(cfg)['macro_timeframe']==tf


@pytest.mark.parametrize('key,value',[('MACRO_TIMEFRAME','5m'),('EVAL_TIMEFRAME','7h'),('MACRO_EMA_PERIOD','0')])
def test_invalid_compass_is_refused_before_runtime(key,value):
    with pytest.raises((ValueError,RuntimeError)):
        load_settings(use_environment=False,overrides={key:value})


@pytest.mark.asyncio
async def test_runtime_switches_cadence_when_flat_and_defers_with_open_positions():
    initial=config(); requested=config(EVAL_TIMEFRAME='1h',MACRO_TIMEFRAME='1h',MACRO_EMA_PERIOD='75')
    ledger=FeatureLedger(now=NOW)
    ledger.runtime_settings=AsyncMock(return_value=({'EVAL_TIMEFRAME':'1h','MACRO_TIMEFRAME':'1h','MACRO_EMA_PERIOD':'75'},NOW))
    ledger.open_positions=AsyncMock(return_value=[object()])
    ledger.acknowledge_settings=AsyncMock()
    runtime=Orchestrator(initial,ledger,object(),object(),PaperBroker(Decimal('.008'),5),
                         settings_loader=lambda **kwargs: requested, insights=False)
    changed,error=await runtime.reload_configuration()
    assert not changed and 'différé' in error and runtime.decision_timeframe=='4h'
    ledger.open_positions.return_value=[]
    changed,error=await runtime.reload_configuration()
    assert changed and error is None and runtime.decision_timeframe=='1h'
    assert runtime._settings.risk.macro_ema_period==75
    assert ledger.acknowledge_settings.call_args.args[2]['eval_timeframe']=='1h'


@pytest.mark.asyncio
async def test_file_reload_keeps_mode_and_only_changes_the_requested_strategy():
    current=[config()]
    runtime=Orchestrator(current[0],FeatureLedger(now=NOW),object(),object(),PaperBroker(Decimal('.008'),5),
                         settings_loader=lambda **kwargs: current[0], insights=False)
    await runtime.reload_configuration()  # référence du fichier
    current[0]=config(MACRO_TIMEFRAME='1h',MACRO_EMA_PERIOD='75',EVAL_TIMEFRAME='1h')
    changed,error=await runtime.reload_configuration()
    assert changed and not error and runtime.decision_timeframe=='1h'
    assert runtime._settings.mode=='paper'


def test_an_open_position_keeps_its_original_macro_policy():
    initial=config(); policy=position_policy(initial)
    next_config=config(MACRO_TIMEFRAME='1h',MACRO_EMA_PERIOD='50')
    managed=apply_policy(next_config,policy)
    assert managed.risk.macro_timeframe=='4h' and managed.risk.macro_ema_period==200
    assert next_config.risk.macro_timeframe=='1h'


@pytest.mark.asyncio
@pytest.mark.parametrize('tf,expected',[('1h','RUNTIME_LATE'),('1d',None)])
async def test_observer_uses_applied_cadence_instead_of_its_startup_clock(tf,expected):
    from app.insights.watch import observe_protection
    now=datetime(2026,11,16,10,0,tzinfo=timezone.utc)
    ledger=FeatureLedger(now=now)
    ledger.settings_state=AsyncMock(return_value={'applied_payload':{'eval_timeframe':tf},
                                                'apply_error':'nouvelle cadence différée'})
    ledger.recent_cycles=AsyncMock(return_value=[{'started_at':now-timedelta(hours=2),'status':'COMPLETE'}])
    result=await observe_protection(ledger,None,'PAPER',now=now,expect_runtime=True,timeframe='4h')
    assert result['observation']['runtime_timeframe']==tf
    keys=[issue['key'] for issue in result['issues']]
    assert keys==([expected] if expected else [])


def test_prompt_and_local_llm_guard_use_the_selected_macro():
    cfg=config(MACRO_TIMEFRAME='1h',MACRO_EMA_PERIOD='50')
    snap=replace(market(ema='110'),macro_timeframe='1h',macro_ema_period=50,
                 macro_ema_value=Decimal(90),eval_timeframe='4h')
    ctx=replace(context(),intel={'sentiment_score':'-.7','catalyst_summary':'Contexte public.'})
    payload,data=SignalClient(cfg.llm)._payload('BTC/EUR',snap,ctx,False,Decimal('.005'))
    assert data['macro']['timeframe']=='1h' and data['macro']['ema_period']==50
    assert data['above_macro_with_buffer'] is True and 'EMA50 1h' in payload['messages'][0]['content']
    assert data['market_intel']['sentiment_score']=='-.7'


@pytest.mark.asyncio
async def test_intel_context_and_veto_are_recorded_and_replayed_without_new_io():
    cfg=config(MARKET_INTEL_ENABLED='true',ENTRY_RULE_ENABLED='false')
    data=historical_data()
    at=datetime.fromtimestamp((data['BTC/EUR']['candles']['4h'][259][0]+14_400_000)/1000, timezone.utc)
    ledger=FeatureLedger(now=at)
    ledger.market_intel=AsyncMock(return_value={'status':'OK','observed_at':at-timedelta(minutes=1),
        'valid_until':at+timedelta(hours=1),'systemic_risk_detected':True,'affected_assets':['BTC'],
        'sentiment_score':Decimal('-.8'),'catalyst_summary':'Faillite d’exchange signalée.'})
    runtime=Orchestrator(cfg,ledger,HistoricalExchange(data,at),ExperimentSignal({'kind':'mechanical'}),
                         PaperBroker(Decimal('.008'),5),now=lambda:at)
    result=await runtime.run_once()
    assert not result.position_opened and not ledger.orders
    assert any('REJECTED_INTEL_SYSTEMIC_RISK' in str(e) for e in ledger.events)
    assert (await replay_cycle(next(iter(ledger.packets.values()))))['status']=='IDENTICAL'

@pytest.mark.asyncio
@pytest.mark.parametrize('tf',['1h','1d'])
async def test_historical_evaluation_clocks_remain_causal(tf):
    from app.backtest.engine import run_backtest, ReplayExchange
    from app.market.feed import TIMEFRAME_MS, build_snapshot
    from tests.test_backtest import candles, market_info, START
    from tests.test_risk_gate import intent
    step=TIMEFRAME_MS[tf]
    eval_series=candles(START,500,step)
    h1=candles(START-timedelta(days=20),500*step//TIMEFRAME_MS['1h']+480,TIMEFRAME_MS['1h'])
    h4=candles(START-timedelta(days=60),500*step//TIMEFRAME_MS['4h']+360,TIMEFRAME_MS['4h'])
    cfg=load_settings(use_environment=False,overrides={'EVAL_TIMEFRAME':tf,'MACRO_TIMEFRAME':tf,'PAPER_START_EQUITY':'1000'})
    all_series={'1h':h1,'4h':h4,tf:eval_series}
    exchange=ReplayExchange(eval_series,h1,decision_timeframe=tf,macro_timeframe=tf,series=all_series)
    close_ms=eval_series[460][0]+step
    exchange.set_now(close_ms)
    snapshot=build_snapshot(exchange,'BTC/EUR',info=market_info(),with_order_book=False,
                            decision_timeframe=tf,macro_timeframe=tf,now=datetime.fromtimestamp(close_ms/1000,timezone.utc))
    assert int(snapshot.last_close_at.timestamp()*1000)==close_ms
    assert all(c[0]+step<=close_ms for c in exchange.fetch_ohlcv('BTC/EUR',tf))
    result=await run_backtest(symbol='BTC/EUR',info=market_info(),h4=eval_series,h1=h1,
                              settings=cfg,propose=None,mechanical=True,series=all_series)
    assert result.equity_start==Decimal(1000)


@pytest.mark.asyncio
async def test_scheduler_recomputes_its_deadline_after_a_hot_clock_change(monkeypatch):
    from types import SimpleNamespace
    import app.main as main
    from app.runtime.orchestrator import CycleReport
    frames=[]; ticks=[0]
    async def sleep(seconds): ticks[0]+=seconds
    monkeypatch.setattr(main,'asyncio',SimpleNamespace(sleep=sleep,get_running_loop=lambda:SimpleNamespace(time=lambda:ticks[0])))
    def close(now,tf): frames.append(tf); return now+timedelta(seconds=60)
    monkeypatch.setattr(main,'next_candle_close',close)
    class Runtime:
        decision_timeframe='4h'
        async def run_once(self): return CycleReport('PAPER','IDLE','IDLE')
        async def reload_configuration(self): self.decision_timeframe='1h'; return True,None
    await main._serve(Runtime(),'4h',max_cycles=2)
    assert frames[0]=='4h' and frames[-1]=='1h'


@pytest.mark.asyncio
async def test_new_systemic_digest_published_during_inference_vetoes_final_entry():
    cfg=config(MARKET_INTEL_ENABLED='true',ENTRY_RULE_ENABLED='false')
    data=historical_data()
    at=datetime.fromtimestamp((data['BTC/EUR']['candles']['4h'][259][0]+14_400_000)/1000,timezone.utc)
    ledger=FeatureLedger(now=at)
    base={'status':'OK','observed_at':at-timedelta(minutes=1),'valid_until':at+timedelta(hours=1),
          'affected_assets':['BTC'],'sentiment_score':Decimal(0),'catalyst_summary':'Observation.'}
    ledger.market_intel=AsyncMock(side_effect=[{**base,'systemic_risk_detected':False},
        {**base,'systemic_risk_detected':True}])
    runtime=Orchestrator(cfg,ledger,HistoricalExchange(data,at),ExperimentSignal({'kind':'mechanical'}),
                         PaperBroker(Decimal('.008'),5),now=lambda:at)
    result=await runtime.run_once()
    assert not result.position_opened and not ledger.orders and ledger.market_intel.await_count==2
    assert any(event['stage']=='REVALIDATION_REJECTED' for event in ledger.events)
    assert (await replay_cycle(next(iter(ledger.packets.values()))))['status']=='IDENTICAL'
