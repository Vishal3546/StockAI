"""FIX108 on-demand selected-stock analysis; bounded expensive work, no trades."""
import threading
from flask import jsonify, request
import daily_context
_GATE=threading.BoundedSemaphore(1)

def register(app, host):
    daily_context.bind(host)
    @app.get('/api/deep-analysis/<symbol>')
    def deep_analysis_api(symbol):
        exchange=request.args.get('exchange','NSE').upper()
        if exchange not in ('NSE','BSE'): return jsonify(error='Invalid exchange'),400
        if not _GATE.acquire(blocking=False):
            response=jsonify(error='Another deep analysis is running. Wait before retrying.');response.headers['Retry-After']='10'
            return response,429
        try:
            import deep_analyzer
            result=deep_analyzer.run_deep_analysis(symbol,exchange,save=False)
            if not result.get('available'): return jsonify(result),503
            return jsonify(result)
        except ValueError as exc: return jsonify(error=str(exc)),400
        except Exception as exc:
            app.logger.warning('Deep analysis unavailable: %s',type(exc).__name__)
            return jsonify(error='Requested-exchange daily analysis unavailable; no fabricated result'),503
        finally: _GATE.release()
