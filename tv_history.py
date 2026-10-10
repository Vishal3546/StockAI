"""FIX-101: historical-chart-only adapter for tradingview-datafeed 2.1.1.

The upstream get_hist also sends quote subscription commands. Those can produce
quote_add_symbols/unknown_session_id errors and disconnect a valid chart fetch.
Omit only the unrelated quote_* messages; preserve auth, symbol resolution,
chart sessions and requested exchange. This grants no additional permissions
and is NOT a real-time quote API. No global monkey patch or site-packages edits.

Private hooks are version-sensitive: requirements.txt pins the tested upstream.
This hotfix deliberately leaves timestamp parsing and exchange/stale policies
unchanged. See FIX101_RELEASE_NOTES.md for outstanding issues.
"""
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

    def get_hist(self, *args, **kwargs):
        symbol = kwargs.get('symbol', args[0] if args else '?')
        exchange = kwargs.get('exchange', args[1] if len(args) > 1 else 'NSE')
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
