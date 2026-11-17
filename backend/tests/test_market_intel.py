"""Veille publique bornée, repli neutre et veto sans achat forcé."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import httpx
import pytest
from app.intel.collector import parse_rss, collect, parse_fng, FNG_URL, article_url
from app.intel.models import MarketIntelDigest, systemic_for, neutral
from app.intel.worker import analyze
from app.intel.store import store_within_budget, BudgetExceeded
from app.engine.risk_gate import RejectionCode
from tests.test_risk_gate import NOW, market, run
from app.core.settings import load_settings


def rss():
    return b'<rss><channel><item><title>Network outage</title><description>Public report</description><pubDate>Wed, 04 Nov 2026 11:00:00 GMT</pubDate></item><item><title>Old</title><pubDate>Mon, 02 Nov 2026 11:00:00 GMT</pubDate></item></channel></rss>'


def test_rss_is_causal_and_hashes_same_headline_identically():
    a=parse_rss(rss(),now=NOW,source='one'); b=parse_rss(rss(),now=NOW,source='two')
    assert len(a)==1 and a[0]['sha256']==b[0]['sha256'] and len(a[0]['sha256'])==64
    assert parse_rss(rss(),now=NOW-timedelta(hours=2))==[]


@pytest.mark.parametrize('body',[b'<!DOCTYPE rss [<!ENTITY x "attack">]><rss/>', b'<rss/>'.decode().encode('utf-16')])
def test_rss_rejects_entities_and_ambiguous_encoding(body):
    with pytest.raises(ValueError): parse_rss(body,now=NOW)


@pytest.mark.asyncio
async def test_collector_degrades_on_fng_timestamp_outside_platform_range():
    def request(req):
        if str(req.url) == FNG_URL:
            return httpx.Response(200, json={'data': [{'value': '22', 'timestamp': '999999999999999999999'}]})
        return httpx.Response(200, content=rss())
    data = await collect(now=NOW, transport=httpx.MockTransport(request))
    assert not data['complete'] and data['fear_and_greed'] is None
    assert len(data['articles']) == 1


@pytest.mark.asyncio
async def test_collector_orders_and_deduplicates_by_absolute_time_before_limit():
    old = '<item><title>Old {}</title><pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate></item>'
    recent = '<item><title>Latest</title><pubDate>Wed, 04 Nov 2026 06:30:00 -0500</pubDate></item>'
    first = '<rss><channel>' + ''.join(old.format(i) for i in range(80)) + recent + '</channel></rss>'
    second = '<rss><channel><item><title>Latest</title><pubDate>Wed, 04 Nov 2026 11:00:00 GMT</pubDate></item></channel></rss>'
    def request(req):
        if str(req.url) == FNG_URL:
            return httpx.Response(200, json={'data': [{'value': '22', 'timestamp': str(int(NOW.timestamp()))}]})
        return httpx.Response(200, text=first if 'coindesk' in str(req.url) else second)
    data = await collect(now=NOW, transport=httpx.MockTransport(request))
    assert data['complete'] and len(data['articles']) == 80
    assert data['fear_and_greed']['value'] == 22
    assert data['articles'][0]['title'] == 'Latest'
    assert data['articles'][0]['published_at'] == (NOW - timedelta(minutes=30)).isoformat()


@pytest.mark.asyncio
async def test_offline_is_neutral_and_never_calls_the_model():
    calls=[]
    def request(req):
        calls.append(str(req.url)); raise httpx.ConnectError('offline')
    row=await analyze(load_settings(use_environment=False),now=NOW,transport=httpx.MockTransport(request))
    assert row['status']=='DEGRADED' and row['overall_sentiment']=='NEUTRAL'
    assert row['sentiment_score']==0 and not row['systemic_risk_detected']
    assert len(calls)==3 and all('/api/chat' not in url for url in calls)


@pytest.mark.asyncio
async def test_constrained_model_analysis_is_bounded_and_scoped():
    captured=[]
    cfg=load_settings(overrides={'TRADING_SYMBOLS':'BTC/USDT'},use_environment=False)
    digest={**neutral().model_dump(),'overall_sentiment':'CRISIS','sentiment_score':-.8,
            'systemic_risk_detected':True,'affected_assets':['BTC'],'catalyst_summary':'Arrêt de réseau signalé.'}
    def request(req):
        if req.method=='POST':
            captured.append(json.loads(req.content)); return httpx.Response(200,json={'message':{'content':json.dumps(digest)}})
        if str(req.url)==FNG_URL:
            return httpx.Response(200,json={'data':[{'value':'25','timestamp':str(int(NOW.timestamp()))}]})
        return httpx.Response(200,content=rss())
    row=await analyze(cfg,now=NOW,transport=httpx.MockTransport(request))
    assert row['status']=='OK' and systemic_for(row,'BTC/USDT',NOW)
    assert not systemic_for(row,'ETH/USDT',NOW)
    assert not systemic_for(row,'BTC/USDT',NOW+timedelta(hours=2))
    assert captured[0]['model']==cfg.intel.model and captured[0]['format']['additionalProperties'] is False
    assert cfg.intel.model==cfg.llm.model=='qwen3.5:9b' and captured[0]['think'] is False
    prompt=json.loads(captured[0]['messages'][1]['content'])
    assert prompt['articles']==[{'title':'Network outage','description':'Public report'}]
    assert 'sha256' not in prompt['articles'][0] and len(row['source_hashes'])==1
    assert row['source_items'][0]['title']=='Network outage'
    assert json.loads(row['analysis_response'])==digest and row['analysis_error'] is None
    assert row['fear_and_greed_value']==25 and row['fear_and_greed_at']==NOW


@pytest.mark.parametrize('url',['javascript:alert(1)','http://coindesk.com/news','https://coindesk.com.evil.test/news','https://user:pass@coindesk.com/news','https://coindesk.com:123/news'])
def test_article_links_refuse_unsafe_protocols_and_publishers(url):
    assert article_url(url) is None


def test_rss_preserves_editorial_link_and_its_publication_date():
    body=rss().replace(b'<title>Network outage</title>',b'<title>Network outage</title><link>https://www.coindesk.com/news/report</link>')
    item=parse_rss(body,now=NOW,source='CoinDesk')[0]
    assert item['url']=='https://www.coindesk.com/news/report' and item['published_at']==(NOW-timedelta(hours=1)).isoformat()


@pytest.mark.asyncio
async def test_quota_counts_source_snapshot_larger_than_the_old_fixed_reservation():
    class Conn:
        @asynccontextmanager
        async def transaction(self): yield
        execute=AsyncMock()
        fetchval=AsyncMock(return_value=399_930_000)
    conn=Conn()
    with pytest.raises(BudgetExceeded):
        await store_within_budget(conn,{'observed_at':NOW,'source_items':[{'description':'x'*1000} for _ in range(80)]})
    assert len(conn.fetchval.call_args_list)==1


@pytest.mark.parametrize('change',[{'sentiment_score':float('nan')},{'systemic_risk_detected':'true'}, {'catalyst_summary':'x'*241}, {'sentiment_score':True}])
def test_digest_rejects_malformed_model_numbers_and_booleans(change):
    with pytest.raises(ValueError): MarketIntelDigest.model_validate({**neutral().model_dump(),**change})


def test_systemic_veto_follows_technical_filter_and_never_forces_a_buy():
    assert run(m=replace(market(),intel_systemic_risk=True)).rejection_code is RejectionCode.REJECTED_INTEL_SYSTEMIC_RISK
    assert run(m=replace(market(price='80'),intel_systemic_risk=True)).rejection_code is RejectionCode.REJECTED_COUNTER_TREND_EMA200
    assert run(m=replace(market(price='80'),intel_systemic_risk=False)).rejection_code is RejectionCode.REJECTED_COUNTER_TREND_EMA200
    assert run(m=replace(market(),intel_systemic_risk=True,intel_valid_until=NOW)).approved


@pytest.mark.asyncio
async def test_quota_refusal_prevents_insert_even_if_floor_config_is_lower():
    class Conn:
        @asynccontextmanager
        async def transaction(self): yield
        execute=AsyncMock()
        fetchval=AsyncMock(return_value=400_000_000)
    conn=Conn()
    with pytest.raises(BudgetExceeded):
        await store_within_budget(conn,{'observed_at':NOW},min_free_mb=0)
    assert not any('INSERT' in str(call) for call in conn.fetchval.call_args_list)
