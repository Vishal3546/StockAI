"""FIX-101: historical-chart-only adapter for tradingview-datafeed 2.1.1.

The upstream get_hist also sends quote subscription commands. Those can produce
quote_add_symbols/unknown_session_id errors and disconnect a valid chart fetch.
Omit only the unrelated quote_* messages; preserve auth, symbol resolution,
chart sessions and requested exchange. This grants no additional permissions
and is NOT a real-time quote API. No global monkey patch or site-packages edits.

Private hooks are version-sensitive: requirements.txt pins the tested upstream.
FIX-102 parses completed chart epochs into timezone-aware IST, independent of the host timezone.
"""
import json
import re
import math
import pandas as pd
from safety import checked_symbol
import logging
import threading
from tvDatafeed import TvDatafeed as _Upstream

_LOG = logging.getLogger(__name__)


class HistoryDatafeed(_Upstream):
    def __init__(self, *args, **kwargs):
        if not callable(getattr(_Upstream, '_TvDatafeed__send_message', None)):
            raise RuntimeError('Unsupported tvDatafeed API; verify the pinned dependency')
        super().__init__(*args, **kwargs)
        self._history_lock = threading.RLock()

    def _TvDatafeed__send_message(self, func, args):
        # Only chart history is requested by this adapter. Quote subscriptions
        # are unrelated and their protocol failure can terminate this socket.
        if func.startswith('quote_'):
            return None
        return super()._TvDatafeed__send_message(func, args)

    @staticmethod
    def _TvDatafeed__create_df(raw_data, symbol):
        # Read wire epoch seconds directly, never host-local fromtimestamp().
        # Incomplete/error chart replies must not become apparently valid history.
        rows = {}
        complete = False
        decoder = json.JSONDecoder()
        for header in re.finditer(r'~m~\d+~m~', raw_data):
            try:
                message, _ = decoder.raw_decode(raw_data[header.end():])
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            method = message.get('m')
            if method in ('symbol_error', 'series_error', 'protocol_error', 'critical_error'):
                _LOG.warning('TradingView chart refused/incomplete: %s', method)
                return None
            if method == 'series_completed':
                complete = True
            if method not in ('timescale_update', 'du'):
                continue
            params = message.get('p') or []
            if len(params) < 2 or not isinstance(params[1], dict):
                continue
            for series in params[1].values():
                if not isinstance(series, dict):
                    continue
                for point in series.get('s', []):
                    values = point.get('v') if isinstance(point, dict) else None
                    try:
                        if not values or len(values) < 6:
                            return None
                        row = [float(x) for x in values[:6]]
                        if not all(math.isfinite(x) for x in row):
                            return None
                        rows[row[0]] = row[1:]
                    except (TypeError, ValueError, OverflowError):
                        return None
        if not complete or not rows:
            return None
        epochs = sorted(rows)
        index = pd.to_datetime(epochs, unit='s', utc=True).tz_convert('Asia/Kolkata')
        frame = pd.DataFrame([rows[t] for t in epochs], index=index,
                             columns=['open','high','low','close','volume'])
        frame.index.name = 'datetime'
        frame.insert(0, 'symbol', symbol)
        frame.attrs['adjustment'] = 'TradingView requested splits adjustment; not independently reconciled'
        return frame

    def get_hist(self, *args, **kwargs):
        symbol = kwargs.get('symbol', args[0] if args else '?')
        exchange = kwargs.get('exchange', args[1] if len(args) > 1 else 'NSE')
        checked_symbol(symbol, exchange)  # reject explicit cross-exchange identifiers
        # The base library mutates self.ws and session state; do not interleave
        # calls on one client, including standalone scanner/research callers.
        with self._history_lock:
            try:
                result = super().get_hist(*args, **kwargs)
                if result is None or result.empty:
                    _LOG.warning('TradingView chart history unavailable: exchange=%r symbol=%r; '
                                 'empty response does not establish an unlisted symbol', exchange, symbol)
                return result
            except Exception as exc:
                # Do not print credentials, headers, tokens or raw exception text.
                _LOG.warning('TradingView chart request failed: exchange=%r symbol=%r error=%s',
                             exchange, symbol, type(exc).__name__)
                raise
            finally:
                ws = getattr(self, 'ws', None)
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        _LOG.debug('TradingView chart socket close failed')
                self.ws = None
