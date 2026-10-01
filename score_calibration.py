"""FIX-33: honest, reproducible cross-sectional score calibration.

The LIVE ranked score and each HISTORICAL score use exactly the same four
*DAILY* engines, 250-bar trailing OHLCV window and scoring function below.
Market Regime is a market-wide exposure gate; intraday MTF cannot be replayed
from a 250-session daily history, so it remains a separately shown diagnostic.

A percentile is a RELATIVE RANK, not P(profit) or a backtested trading edge.
The JSON artifact contains each historical universe snapshot; no scanner ML
`composite`, current-bar price or synthetic minute data is used for fitting.
"""

import bisect
import math
from datetime import date, datetime, timezone
from pathlib import Path

UNIVERSE = (
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK',
    'SBIN', 'BHARTIARTL', 'ITC', 'KOTAKBANK', 'LT',
    'WIPRO', 'AXISBANK', 'MARUTI', 'TATAMOTORS', 'BAJFINANCE',
    'SUNPHARMA', 'TITAN', 'ADANIENT', 'POWERGRID', 'NTPC',
    'ONGC', 'COALINDIA', 'TATASTEEL', 'TECHM', 'ASIANPAINT',
    'ULTRACEMCO', 'NESTLEIND', 'BAJAJFINSV', 'DRREDDY', 'JSWSTEEL'
)

DAILY_WEIGHTS = {
    'Volume Profile': 0.12,
    'RVOL + CVD + VSA': 0.20,
    'VCP V2': 0.15,
    'SMC / ICT': 0.15,
}
MODEL = 'daily4-trailing250-v1'
LOOKBACK_BARS = 250
WINDOW_SESSIONS = 250
MIN_COVERAGE = 20
MAX_AGE_CALENDAR_DAYS = 10
ARTIFACT_PATH = Path(__file__).resolve().with_name('score_calibration.json')
PERCENTILES = {
    'short_sell': 0.10,     # bottom decile (relative rank only)
    'watchlist': 0.40,
    'buy_dip': 0.80,        # replaces arbitrary score >=65
    'buy_breakout': 0.95,   # replaces arbitrary score >=78
}


# ── H-11: engine-level audit ───────────────────────────────────────────────
# Composite score akela kaafi nahi batata — kaunsa engine actually stocks ko
# ALAG karta hai aur kaunsa sabko ek jaisa number dekar sirf offset jodta hai,
# ye per-engine cross-sectional dispersion se hi pata chalta hai. Isliye builder
# ab har session/symbol ke chaaron engine scores bhi store karta hai (chhote keys)
# taaki weights ka faisla offline, bina history dobara download kiye, re-analyse
# ho sake — aur stored composite ko engine scores se dobara banakar verify kiya
# ja sake.
ENGINE_KEYS = {
    'vp': 'Volume Profile',
    'rvol': 'RVOL + CVD + VSA',
    'vcp': 'VCP V2',
    'smc': 'SMC / ICT',
}
ENGINE_KEYS_REV = {v: k for k, v in ENGINE_KEYS.items()}


class CalibrationError(ValueError):
    """Never silently use a missing, stale or incomparable calibration."""


def stock_rank(engines):
    """Same daily-engine ranking formula in the live API AND as-of backfill.

    Engine list may also contain MTF / Market Regime — both ignored here.
    Missing/degraded stock engines are excluded & the remaining weights
    renormalised. That *partial* score is displayed, but NOT calibrated for
    actions (historical universe snapshots require all four daily engines).
    """
    daily = {e['name']: e for e in engines if e.get('name') in DAILY_WEIGHTS}
    excluded, live = [], []
    for name, weight in DAILY_WEIGHTS.items():
        e = daily.get(name)
        s = e.get('score') if e else None
        if (e is None or e.get('degraded') or isinstance(s, bool)
                or not isinstance(s, (int, float)) or not math.isfinite(s)
                or s < 5 or s > 98):
            excluded.append(name)
        else:
            live.append(e)
    if not live:
        return {'score': None, 'bullish_engines': 0, 'bearish_engines': 0,
                'confluence_bonus': 0, 'engines_used': 0,
                'degraded_engines': excluded, 'complete': False,
                'note': 'Stock engines unavailable — no master score, no trade'}

    ws = sum(e['score'] * DAILY_WEIGHTS[e['name']] for e in live)
    tw = sum(DAILY_WEIGHTS[e['name']] for e in live)
    base = ws / tw
    bullish = sum(e['score'] >= 65 for e in live)
    bearish = sum(e['score'] <= 35 for e in live)
    # Same confluence policy, now applied only to four stock-specific engines.
    bonus = 5 if bullish >= 4 else 2 if bullish >= 3 else -8 if bearish >= 4 else 0
    score = int(max(5, min(98, base + bonus)))
    return {
        'score': score, 'bullish_engines': bullish,
        'bearish_engines': bearish, 'confluence_bonus': bonus,
        'engines_used': len(live), 'degraded_engines': excluded,
        'complete': len(live) == len(DAILY_WEIGHTS),
        'note': (f'Degraded engines averaging se exclude kiye: {", ".join(excluded)}'
                 if excluded else None)
    }


def stock_rank_from_map(engine_map):
    """{'vp': 52, 'rvol': 61, …} → stock_rank() — offline recomputation."""
    engines = [{'name': ENGINE_KEYS[k], 'score': v}
               for k, v in (engine_map or {}).items() if k in ENGINE_KEYS]
    return stock_rank(engines)


def verify_engine_history(history):
    """Har stored composite ko engine scores se dobara banao; mismatch count lao.

    0 mismatch ka matlab stored engine data aur displayed score ek hi cheez hain
    (koi alag/seeded number nahi). Non-zero matlab artifact edited/adhoora hai.
    """
    bad = 0
    for row in history or ():
        engines = row.get('engines') if isinstance(row, dict) else None
        if not isinstance(engines, dict):
            continue
        for symbol, composite in (row.get('scores') or {}).items():
            recomputed = stock_rank_from_map(engines.get(symbol))
            if recomputed.get('score') != composite:
                bad += 1
    return bad


def _std(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def engine_dispersion(history):
    """Per-engine cross-sectional dispersion over the fitted window.

    Har session me ek engine ke scores ka spread (std) nikalte hain — wahi spread
    ranking me information hai. Sab stocks ko ~same number dene wala engine
    cross-sectionally flat hota hai: wo har stock par same offset jodta hai, isliye
    *relative rank* me kuch nahi badalta (sirf absolute score scale badalta hai).

    Return per engine: pooled_std · mean/min/max session std · unique values ·
    flat_sessions (std < 2 wale sessions ka share) · observed range.
    """
    stats = {k: {'pooled': [], 'session_std': [], 'values': set()}
             for k in ENGINE_KEYS}
    for row in history or ():
        engines = row.get('engines') if isinstance(row, dict) else None
        if not isinstance(engines, dict):
            continue
        per_key = {k: [] for k in ENGINE_KEYS}
        for engine_map in engines.values():
            if not isinstance(engine_map, dict):
                continue
            for k, v in engine_map.items():
                if k in ENGINE_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                    per_key[k].append(v)
                    stats[k]['pooled'].append(v)
                    stats[k]['values'].add(v)
        for k, values in per_key.items():
            if len(values) >= 2:
                stats[k]['session_std'].append(_std(values))
    out = {}
    for k, d in stats.items():
        ss = d['session_std']
        pooled = d['pooled']
        if not ss:
            continue
        out[ENGINE_KEYS[k]] = {
            'key': k,
            'pooled_std': round(_std(pooled), 3),
            'mean_session_std': round(sum(ss) / len(ss), 3),
            'min_session_std': round(min(ss), 3),
            'max_session_std': round(max(ss), 3),
            'flat_session_share': round(sum(1 for x in ss if x < 2.0) / len(ss), 4),
            'unique_values': len(d['values']),
            'min_value': min(pooled), 'max_value': max(pooled),
            'sessions': len(ss),
        }
    return out


def _quantile(sorted_values, probability):
    """Type-7 / linear interpolation; does not add fictional observations."""
    pos = (len(sorted_values) - 1) * probability
    lo, hi = math.floor(pos), math.ceil(pos)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def fit_history(history, *, min_sessions=WINDOW_SESSIONS,
                min_coverage=MIN_COVERAGE):
    """Compute thresholds from completed universe snapshots only.

    Raises instead of pretending one scanner run / 29 rows is 250-day history.
    No imputation for missing dates/symbols; no same-day future bar in builder.
    """
    if not isinstance(history, list) or len(history) < min_sessions:
        raise CalibrationError(f'need {min_sessions} eligible sessions; got {len(history) if isinstance(history, list) else 0}')
    if len(history) > WINDOW_SESSIONS:
        raise CalibrationError('more than 250 sessions — rebuild rolling window')
    pooled, seen, last_day = [], set(), None
    for row in history:
        if not isinstance(row, dict):
            raise CalibrationError('history row is not a dict')
        session = row.get('session')
        try:
            day = date.fromisoformat(session)
            if day.isoformat() != session or (last_day is not None and day <= last_day):
                raise ValueError('duplicate, unordered or non-ISO session')
        except (TypeError, ValueError) as exc:
            raise CalibrationError(f'invalid session {session!r}') from exc
        if day.weekday() > 4:
            raise CalibrationError(f'{session}: NSE cash session cannot be a weekend')
        last_day = day
        scores = row.get('scores')
        if not isinstance(scores, dict) or len(scores) < min_coverage:
            raise CalibrationError(f'{session}: fewer than {min_coverage} universe stocks')
        if not set(scores).issubset(set(UNIVERSE)):
            raise CalibrationError(f'{session}: unknown universe symbol')
        for symbol, score in scores.items():
            if isinstance(score, bool) or not isinstance(score, int) or not 5 <= score <= 98:
                raise CalibrationError(f'{session}/{symbol}: non-finite/non-integral score')
            pooled.append(score)
            seen.add(symbol)
    if len(seen) < min_coverage:
        raise CalibrationError('coverage across all sessions too small')
    pooled.sort()
    thresholds = {k: round(_quantile(pooled, p), 6) for k, p in PERCENTILES.items()}
    if not (thresholds['short_sell'] < thresholds['watchlist']
            < thresholds['buy_dip'] < thresholds['buy_breakout']):
        raise CalibrationError('score distribution too tied to separate the four bands')
    return {
        'thresholds': thresholds, 'samples': len(pooled),
        'sessions': len(history), 'universe_covered': len(seen),
        'symbols_covered': tuple(sorted(seen)),
        'asof_session': history[-1]['session'], '_sorted_scores': pooled,
    }


def build_artifact(history, formula_hash, source, *, engine_dispersion_block=None):
    """CLI writes this audited JSON atomically; include score history, not just cutoffs."""
    fitted = fit_history(history)
    mismatch = verify_engine_history(history)
    if mismatch:
        raise CalibrationError(
            f'{mismatch} stored composite scores engine history se match nahi karte')
    if not isinstance(formula_hash, str) or len(formula_hash) < 12:
        raise CalibrationError('missing formula hash')
    return {
        'schema': 1, 'model': MODEL, 'formula_hash': formula_hash,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source': source, 'universe': list(UNIVERSE),
        'weights': DAILY_WEIGHTS, 'lookback_bars': LOOKBACK_BARS,
        'history': history, 'asof_session': fitted['asof_session'],
        'thresholds': fitted['thresholds'], 'samples': fitted['samples'],
        'sessions': fitted['sessions'],
        'engine_keys': ENGINE_KEYS,
        # H-11: per-engine cross-sectional dispersion (weight decisions ka basis)
        'engine_dispersion': engine_dispersion_block or engine_dispersion(history),
    }


def validate_artifact(artifact, formula_hash):
    """Reject edited/old/different score scale; recompute quantiles from stored history."""
    if not isinstance(artifact, dict) or artifact.get('schema') != 1:
        raise CalibrationError('artifact missing/schema mismatch')
    if (artifact.get('model') != MODEL or artifact.get('formula_hash') != formula_hash
            or artifact.get('weights') != DAILY_WEIGHTS
            or artifact.get('lookback_bars') != LOOKBACK_BARS
            or artifact.get('universe') != list(UNIVERSE)):
        raise CalibrationError('score definition/weights/universe changed — rebuild calibration')
    fitted = fit_history(artifact.get('history'))
    if (artifact.get('asof_session') != fitted['asof_session']
            or artifact.get('samples') != fitted['samples']
            or artifact.get('sessions') != fitted['sessions']
            or artifact.get('thresholds') != fitted['thresholds']):
        raise CalibrationError('artifact thresholds/metadata do not match history')
    return fitted


def for_session(fitted, reference_session, *, bars=None, current_day=None):
    """Stock bar post-dates training; optionally also check ACTUAL IST clock.

    A stale source could serve a historical stock bar whose gap from an old
    calibration is 1 day even MONTHS later. Production must pass current_day
    as an independent wall-clock guard; tests/replays may omit it.
    """
    if bars is not None and bars < LOOKBACK_BARS:
        raise CalibrationError(f'only {bars} daily bars < {LOOKBACK_BARS} reference window')
    try:
        current = date.fromisoformat(reference_session)
        asof = date.fromisoformat(fitted['asof_session'])
        delta = (current - asof).days
        wall_age = (date.fromisoformat(current_day) - asof).days if current_day else delta
    except (TypeError, ValueError, KeyError) as exc:
        raise CalibrationError('reference session / current day unavailable') from exc
    if delta <= 0:
        raise CalibrationError('same-day/future history would leak into current label')
    if delta > MAX_AGE_CALENDAR_DAYS or wall_age > MAX_AGE_CALENDAR_DAYS:
        raise CalibrationError(f'calibration stale ({max(delta, wall_age)} calendar days) — run builder again')
    if wall_age < 0 or (current_day is not None and current > date.fromisoformat(current_day)):
        raise CalibrationError('future stock/calibration session vs IST clock')
    return fitted


def label(score, thresholds):
    """Bands are score percentiles, NOT expected return or trading accuracy."""
    if score is None:
        return 'DATA_UNAVAILABLE'
    if score >= thresholds['buy_breakout']:
        return 'BUY_BREAKOUT'
    if score >= thresholds['buy_dip']:
        return 'BUY_DIP'
    if score >= thresholds['watchlist']:
        return 'WATCHLIST'
    if score > thresholds['short_sell']:
        return 'AVOID'
    return 'SHORT_SELL'


def percentile_rank(sorted_scores, score):
    """ECDF (ties at upper rank); descriptive placement, not win probability."""
    return round(100 * bisect.bisect_right(sorted_scores, score) / len(sorted_scores), 1)
