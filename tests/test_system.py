import base64
import json
import math
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from xrpbot.data import Candle, aggregate, read_csv, validate, write_csv, Coinbase, BinanceUS
from xrpbot.engine import Account, Config, PaperBroker, Store
from xrpbot.paper import tick
from xrpbot.research import backtest, chronological_folds, metrics, monte_carlo
from xrpbot.signals import indicators, analyze
from xrpbot.web import server


def bars(n=300, price=2, start=0):
    return [Candle(start+i*60, price, price+.01, price-.01, price, 1000) for i in range(n)]


class DataTests(unittest.TestCase):
    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            write_csv(d+'/x.csv', bars())
            self.assertEqual(read_csv(d+'/x.csv'), bars())

    def test_bad_envelope(self):
        with self.assertRaises(ValueError): validate([Candle(0, 2, 1, 1, 2, 1)])

    def test_gap_duplicate_nan(self):
        for sample in ([bars()[0], bars()[2]], [bars()[0]]*2, [replace(bars()[0], close=math.nan)]):
            with self.assertRaises(ValueError): validate(sample)

    def test_aggregate_excludes_partial(self):
        self.assertEqual(len(aggregate(bars(299), 300)), 59)
        self.assertEqual(len(aggregate(bars(240), 14400)), 1)
        self.assertEqual(aggregate(bars(239, start=60), 14400), [])

    def test_indicator_flat_market(self):
        i = indicators(bars())
        self.assertEqual(i['rsi'], 50)
        self.assertEqual(i['adx'], 0)
        self.assertAlmostEqual(i['vwap'], 2)
        self.assertAlmostEqual(i['atr'], .02)

    def test_warmup_cash(self):
        self.assertEqual(analyze(bars())['action'], 'hold')

    def test_pair_verification(self):
        with patch('xrpbot.data.public_json', return_value={'id':'XRP-USD','status':'offline'}):
            with self.assertRaises(ValueError): Coinbase().product()
        with patch('xrpbot.data.public_json', return_value={'symbols':[]}):
            with self.assertRaises(ValueError): BinanceUS().product()


class RiskTests(unittest.TestCase):
    def setUp(self): self.b = PaperBroker()

    def test_size_includes_fees_and_stop_cost(self):
        b = self.b
        self.assertTrue(b.buy(2, .02, 0, 'a', 'test'))
        a, c = b.account, b.config
        projected = a.quantity * a.stop * (1-(c.spread_bps/2+c.slippage_bps)/10000)*(1-c.fee)
        self.assertLessEqual(10000-a.cash-projected, 50.000001)
        self.assertGreaterEqual(a.cash, 0)

    def test_no_pyramiding_or_duplicate(self):
        self.b.buy(2,.02,0,'a','test')
        self.assertFalse(self.b.buy(2,.02,1000,'b','test'))
        self.b.sell(2,1,'exit','s')
        self.assertFalse(self.b.buy(2,.02,1000,'a','test'))

    def test_gap_stop(self):
        self.b.buy(2,.02,0,'a','test')
        self.b.on_bar(Candle(60,1.8,1.85,1.7,1.8,100))
        self.assertEqual(self.b.account.quantity,0)
        self.assertLess(self.b.account.trades[-1]['price'],1.8)

    def test_daily_latch_and_resume_not_automatic(self):
        self.b.mark(2,0)
        self.b.account.cash=9700
        self.b.mark(2,60)
        self.assertTrue(self.b.account.halted)
        self.b.mark(2,86400)
        self.assertFalse(self.b.buy(2,.02,86400,'a','test'))

    def test_drawdown_latch(self):
        self.b.mark(2,0)
        self.b.account.cash=8900
        self.b.mark(2,86400)
        self.assertTrue(self.b.account.halted)

    def test_stale_future_crossed_quote(self):
        for q in ({'bid':2,'ask':2.01,'timestamp':0}, {'bid':2,'ask':1,'timestamp':1000}, {'bid':2,'ask':2,'timestamp':1010}):
            with self.assertRaises(ValueError): self.b.quote_guard(q,1000)

    def test_limits_partial_and_precision(self):
        self.assertFalse(self.b.buy(2,.02,0,'a','test',limit=1.9,low=1.95))
        self.assertTrue(self.b.buy(2,.02,0,'a','test',limit=1.9,low=1.8,capacity=3.12345678))
        self.assertLessEqual(self.b.account.entry,1.9)
        self.assertEqual(self.b.account.quantity,3.123456)

    def test_minimum_and_cost_limits(self):
        self.assertFalse(self.b.buy(2,.02,0,'a','test',capacity=.1))
        self.assertFalse(PaperBroker(Config(fee=.02)).buy(2,.02,0,'a','test'))

    def test_pause_preserves_stop(self):
        self.b.buy(2,.02,0,'a','test')
        self.b.account.paused=True
        self.b.on_bar(Candle(60,2,2,1.8,1.9,100))
        self.assertEqual(self.b.account.quantity,0)

    def test_invalid_settings(self):
        for settings in ({'risk_per_trade':.5}, {'fee':math.nan}, {'quantity_step':0}, {'max_trades_day':0}):
            with self.assertRaises(ValueError): Config(**settings)

    def test_transaction_rollback_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/x.sqlite')
            s.transact(lambda p:p.update(cash=100))
            def fail(p):
                p['cash']=0
                raise ValueError('abort')
            with self.assertRaises(ValueError): s.transact(fail)
            self.assertEqual(Store(d+'/x.sqlite').transact(lambda p:p['cash']),100)


class ResearchTests(unittest.TestCase):
    def test_next_bar_execution_and_costs(self):
        data=bars(3)
        data[1]=Candle(60,2.1,2.11,2.09,2.1,10000)
        data[2]=Candle(120,2.1,2.11,2.09,2.1,10000)
        def signal(prefix): return {'action':'buy','atr':.1,'reason':'test'}
        r=backtest(data,signal_fn=signal)
        entry=r['trades'][0]
        self.assertEqual(entry['timestamp'],60)
        self.assertGreater(entry['price'],2.1)
        self.assertGreater(r['metrics']['fees_paid'],0)
        self.assertLess(r['metrics']['net_profit'],0)

    def test_future_prices_do_not_change_earlier_fills(self):
        def signal(prefix):
            return {'action':'buy','atr':.1,'reason':'prefix'} if len(prefix)==1 else {'action':'hold'}
        a=bars(6)
        b=a[:4]+bars(2,price=3,start=240)
        ra,rb=backtest(a,signal_fn=signal),backtest(b,signal_fn=signal)
        self.assertEqual(ra['trades'][0],rb['trades'][0])
        self.assertEqual(ra['equity'][:4],rb['equity'][:4])

    def test_purged_folds(self):
        folds=list(chronological_folds(100,40,20,2))
        self.assertEqual(folds[0],((0,40),(42,62)))
        self.assertTrue(all(a[1]<b[0] for a,b in folds))

    def test_no_trade_metrics_honest(self):
        r=backtest(bars())
        self.assertEqual(r['metrics']['closed_trades'],0)
        self.assertIsNone(r['metrics']['win_rate'])
        self.assertIsNone(r['metrics']['sharpe'])

    def test_monte_carlo_reproducible(self):
        trades=[{'side':'sell','pnl':v} for v in (20,-10,30,-40)]
        self.assertEqual(monte_carlo(trades,simulations=20),monte_carlo(trades,simulations=20))


class PaperTests(unittest.TestCase):
    def test_stale_tick_does_not_fill(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/x.sqlite')
            adapter=type('Adapter',(),{'quote':lambda self:{'bid':2,'ask':2.001,'timestamp':1000},'symbol':'XRP-USD'})()
            tick(store,adapter,bars(2),now=1000)
            self.assertIn('Stale',store.transact(lambda p:p['error']))
            self.assertEqual(store.transact(lambda p:p['account']['quantity']),0)

    def test_emergency_flattens_at_next_valid_quote(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/x.sqlite')
            from xrpbot.paper import save_broker
            b=PaperBroker();b.buy(2,.1,0,'a','test');b.account.halted=True
            store.transact(lambda p:save_broker(p,b))
            adapter=type('Adapter',(),{'quote':lambda self:{'bid':2,'ask':2.001,'timestamp':120},'symbol':'XRP-USD'})()
            tick(store,adapter,bars(2),now=120)
            self.assertEqual(store.transact(lambda p:p['account']['quantity']),0)


class WebTests(unittest.TestCase):
    def test_auth_controls_and_latched_halt(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'DASHBOARD_TOKEN':'x'*32}):
            httpd=server(Store(d+'/x.sqlite'),port=0)
            t=threading.Thread(target=httpd.serve_forever,daemon=True);t.start()
            url=f'http://127.0.0.1:{httpd.server_port}'
            auth='Basic '+base64.b64encode(('paper:'+ 'x'*32).encode()).decode()
            try:
                with self.assertRaises(HTTPError) as e: urlopen(url+'/api/state')
                self.assertEqual(e.exception.code,401)
                with urlopen(Request(url+'/',headers={'Authorization':auth})) as r:
                    self.assertIn(b'XRP Paper Desk',r.read())
                with self.assertRaises(HTTPError) as e: urlopen(Request(url+'/api/pause',method='POST',headers={'Authorization':auth}))
                self.assertEqual(e.exception.code,403)
                headers={'Authorization':auth,'X-Paper-Control':'1'}
                with urlopen(Request(url+'/api/emergency',method='POST',headers=headers)) as r:
                    self.assertTrue(json.load(r)['halted'])
                with self.assertRaises(HTTPError) as e: urlopen(Request(url+'/api/resume',method='POST',headers=headers))
                self.assertEqual(e.exception.code,409)
            finally:
                httpd.shutdown();httpd.server_close();t.join()



class ExtendedTests(unittest.TestCase):
    def test_full_multiframe_causality_and_evidence(self):
        history=bars(202*240)
        first=analyze(history)
        self.assertEqual(set(first['indicators']),{'1','5','15','60','240'})
        extended=history+bars(240,price=3,start=len(history)*60)
        self.assertEqual(first,analyze(extended[:len(history)]))
        self.assertEqual(first['regime'],'ranging')
        self.assertEqual(first['action'],'hold')

    def test_ml_scaling_is_train_only(self):
        from xrpbot.ml import evaluate
        a=bars(200)
        b=a[:160]+bars(40,price=3,start=160*60)
        x,y=evaluate(a,epochs=10),evaluate(b,epochs=10)
        self.assertEqual(x['scaling_centers'],y['scaling_centers'])
        self.assertEqual(x['weights'],y['weights'])
        self.assertLess(x['train_last_timestamp']+60,x['test_first_timestamp'])
        self.assertFalse(x['trading_enabled'])

    def test_venue_minimums(self):
        from xrpbot.contracts import product_limits
        self.assertEqual(product_limits('coinbase',{'base_increment':'.000001','min_market_funds':'1'})['min_notional'],1)
        with self.assertRaises((ValueError,KeyError)): product_limits('binanceus',{'filters':[]})

    def test_orderbook_descriptive(self):
        from xrpbot.data import orderbook
        with patch('xrpbot.data.public_json',return_value={'bids':[['2','30','1']],'asks':[['2.01','10','1']]}):
            self.assertEqual(orderbook(Coinbase())['imbalance'],.5)

    def test_frequency_limit(self):
        b=PaperBroker(Config(max_trades_day=1))
        b.buy(2,.02,0,'a','test');b.sell(2,60,'exit','s')
        self.assertFalse(b.buy(2,.02,1000,'b','test'))

    def test_negative_fee_is_rejected(self):
        with self.assertRaises(ValueError): Config(fee=-.01)

    def test_stale_candles_do_not_disable_protective_exit(self):
        with tempfile.TemporaryDirectory() as d:
            from xrpbot.paper import save_broker
            store=Store(d+'/x.sqlite')
            broker=PaperBroker();broker.buy(2,.02,0,'entry','test')
            store.transact(lambda p:save_broker(p,broker))
            adapter=type('Adapter',(),{'quote':lambda self:{'bid':1.9,'ask':1.901,'timestamp':1000},'symbol':'XRP-USD'})()
            tick(store,adapter,bars(2),now=1000)
            state=store.transact(dict)
            self.assertEqual(state['account']['quantity'],0)
            self.assertIn('Stale',state['error'])

    def test_walk_forward_executes_disjoint_folds(self):
        from xrpbot.research import walk_forward
        folds=walk_forward(bars(100),train=40,test=20)
        self.assertEqual(len(folds),2)
        self.assertEqual(folds[0]['test_indices'],(41,61))
        self.assertEqual(folds[1]['test_indices'],(61,81))
        self.assertEqual(folds[0]['metrics']['closed_trades'],0)

    def test_metric_accounting(self):
        trades=[{'side':'sell','pnl':100,'fee':1},{'side':'sell','pnl':-50,'fee':1}]
        m=metrics([(60,10100),(120,10050)],trades,10000,120)
        self.assertEqual(m['closed_trades'],2)
        self.assertEqual(m['net_profit'],50)
        self.assertEqual(m['profit_factor'],2)
        self.assertEqual(m['expectancy'],25)
        self.assertEqual(m['exposure_fraction'],1)

if __name__=='__main__': unittest.main()
