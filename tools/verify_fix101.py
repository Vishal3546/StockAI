"""Offline regressions for FIX-101 chart-only history; no live provider required."""
import pathlib
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from tv_history import HistoryDatafeed, _Upstream


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tv = HistoryDatafeed()
        self.df = pd.DataFrame({'close': [100.]})

    def test_quote_commands_never_sent(self):
        with patch.object(_Upstream, '_TvDatafeed__send_message') as send:
            for method in ('quote_create_session', 'quote_set_fields', 'quote_add_symbols', 'quote_fast_symbols'):
                self.tv._TvDatafeed__send_message(method, ['fixture'])
            send.assert_not_called()

    def test_chart_and_auth_commands_preserved(self):
        with patch.object(_Upstream, '_TvDatafeed__send_message') as send:
            for method in ('set_auth_token', 'chart_create_session', 'resolve_symbol', 'create_series', 'switch_timezone'):
                self.tv._TvDatafeed__send_message(method, ['fixture'])
                send.assert_called_with(method, ['fixture'])
            self.assertEqual(send.call_count, 5)

    def test_upstream_not_globally_patched(self):
        self.assertIsNot(_Upstream._TvDatafeed__send_message, HistoryDatafeed._TvDatafeed__send_message)

    def test_constructor_credentials_forwarded_not_replaced(self):
        with patch.object(_Upstream, '__init__', return_value=None) as init:
            HistoryDatafeed(username='fixture-user', password='fixture-password')
            init.assert_called_once_with(username='fixture-user', password='fixture-password')

    def test_requested_exchange_interval_and_bars_unchanged(self):
        with patch.object(_Upstream, 'get_hist', return_value=self.df) as get:
            result = self.tv.get_hist(symbol='TCS', exchange='BSE', interval='test-interval', n_bars=300)
            self.assertIs(result, self.df)
            get.assert_called_once_with(symbol='TCS', exchange='BSE', interval='test-interval', n_bars=300)

    def test_positional_arguments_unchanged(self):
        with patch.object(_Upstream, 'get_hist', return_value=self.df) as get:
            self.tv.get_hist('JIOFIN', 'NSE')
            get.assert_called_once_with('JIOFIN', 'NSE')

    def test_socket_closed_on_success(self):
        ws = Mock(); self.tv.ws = ws
        with patch.object(_Upstream, 'get_hist', return_value=self.df):
            self.tv.get_hist('TCS', 'BSE')
        ws.close.assert_called_once(); self.assertIsNone(self.tv.ws)

    def test_socket_closed_on_exception(self):
        ws = Mock(); self.tv.ws = ws
        with patch.object(_Upstream, 'get_hist', side_effect=TimeoutError('fixture')):
            with self.assertRaises(TimeoutError): self.tv.get_hist('TCS', 'BSE')
        ws.close.assert_called_once(); self.assertIsNone(self.tv.ws)

    def test_close_exception_does_not_replace_result(self):
        ws = Mock(); ws.close.side_effect = OSError('fixture'); self.tv.ws = ws
        with patch.object(_Upstream, 'get_hist', return_value=self.df):
            self.assertIs(self.tv.get_hist('TCS'), self.df)
        self.assertIsNone(self.tv.ws)

    def test_empty_result_is_not_fabricated(self):
        with patch.object(_Upstream, 'get_hist', return_value=None):
            with self.assertLogs('tv_history', level='WARNING') as logs:
                self.assertIsNone(self.tv.get_hist('TCS', 'BSE'))
            self.assertIn('does not establish an unlisted symbol', ''.join(logs.output))

    def test_same_client_calls_serialized(self):
        active = 0; maximum = 0; guard = threading.Lock()
        def fetch(*args, **kwargs):
            nonlocal active, maximum
            with guard: active += 1; maximum = max(maximum, active)
            time.sleep(.01)
            with guard: active -= 1
            return self.df
        with patch.object(_Upstream, 'get_hist', side_effect=fetch):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: self.tv.get_hist('TCS', 'BSE'), range(8)))
        self.assertEqual(maximum, 1)

    def test_runtime_callers_use_adapter(self):
        for name in ('app.py', 'deep_analyzer.py', 'nifty_scanner.py', 'research/data.py',
                     'tools/study_new_signals.py', 'tools/study_oi_signal.py'):
            source = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('from tv_history import HistoryDatafeed as TvDatafeed', source, name)
            self.assertNotIn('from tvDatafeed import TvDatafeed', source, name)

    def test_readiness_message_does_not_claim_connection(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertNotIn('Connected to TradingView Feed.', source)
        self.assertIn('Provider availability is checked on fetch', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
