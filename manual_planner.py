"""FIX107: manual cash planning. No prediction, Kelly, margin or order authority.

Fee source: https://www.angelone.in/exchange-transaction-charges
DP: https://www.angelone.in/support/charges-and-cashbacks/dp-depository-participant-charges
NSE cap: https://nsearchives.nseindia.com/content/circulars/insp11532.htm
Reviewed 2026-10-11. This profile must be reviewed when tariffs change.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
import math

D = Decimal
PROFILE = 'angel_one_standard_cash_nse_2026-10-11'
NOTES = [
    'Hypothetical sizing only; no signal, win probability, execution approval or broker order.',
    'Standard resident cash account; no introductory offer, NRI, MTF, leverage or overnight short.',
    'One entry order and ONE full-quantity exit. Stop and target are mutually exclusive scenarios, not a scale-out plan.',
    'Delivery assumes settled holdings at sale: one DP debit of ₹20 plus GST. BTST/auction/short-delivery costs are not modelled.',
    '₹1 fee/rounding reserve per order. Contract-note rounding, special plans and actual charges may differ.',
    'Stop loss is not a loss guarantee. Gaps, circuits, illiquidity and failed exits can exceed the budget.',
    'Intraday exit deadline and instrument/product eligibility must be checked in Angel One; no universal cutoff is assumed.',
    'BSE automatic fees are blocked until stock-group charges and sidedness are independently verified.',
]


def number(body, key, lo='0.000001', hi='1000000000', allow_zero=False):
    raw = body.get(key)
    if isinstance(raw, bool) or raw is None:
        raise ValueError(f'{key}: a finite number is required')
    try:
        value = D(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{key}: a finite number is required') from None
    if not value.is_finite() or value > D(hi) or value < D(lo) or (value == D(lo) and not allow_zero):
        raise ValueError(f'{key}: outside permitted range ({lo} to {hi})')
    return value


def fees(price, qty, side, mode):
    """One executed NSE cash order, unrounded components; DP once per sell transaction.
    Aggregated partial fills of one order are NOT separate brokerage orders.
    Low-price NSE cap is 25 paise/share below ₹10 (2.5% at ₹10 is identical).
    """
    if qty == 0:
        return {k: D(0) for k in ('brokerage','transaction','ipft','sebi','stt','stamp','dp','gst','reserve','total')}
    value = price * qty
    cap = D('.25') * qty if price < 10 else value * D('.025')
    brokerage = min(D(20), max(D(5), value * D('.001')), cap)
    transaction = value * D('.000030699')
    ipft = value * D('.000000001')
    sebi = value * D('.000001')
    stt = value * (D('.001') if mode == 'delivery' else D('.00025') if side == 'sell' else D(0))
    stamp = value * (D('.00015') if mode == 'delivery' else D('.00003')) if side == 'buy' else D(0)
    dp = D(20) if mode == 'delivery' and side == 'sell' else D(0)
    gst = (brokerage + transaction + ipft + sebi + dp) * D('.18')
    values = dict(brokerage=brokerage, transaction=transaction, ipft=ipft, sebi=sebi,
                  stt=stt, stamp=stamp, dp=dp, gst=gst, reserve=D(1))
    values['total'] = sum(values.values())
    return values


def plan(body):
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    mode = body.get('mode')
    direction = body.get('direction')
    if mode not in ('intraday', 'delivery') or direction not in ('long', 'short'):
        raise ValueError('Select intraday/delivery and long/short')
    if body.get('exchange') != 'NSE':
        raise ValueError('Automatic fee-aware sizing currently supports NSE only. BSE stock-group charges are not verified; NSE rates will not be substituted.')
    if mode == 'delivery' and direction == 'short':
        raise ValueError('Unborrowed overnight cash short is not supported. Delivery is long only.')
    capital = number(body, 'capital')
    risk = number(body, 'risk_rupees')
    entry = number(body, 'entry', hi='1000000')
    stop = number(body, 'stop', hi='1000000')
    target = number(body, 'target', hi='1000000')
    slip = number(body, 'slippage_bps', lo='0', hi='1000', allow_zero=True) / D(10000)
    if risk > capital:
        raise ValueError('Risk budget must not exceed cash capital')
    long = direction == 'long'
    if not (stop < entry < target if long else target < entry < stop):
        raise ValueError('Long requires stop < entry < target; short requires target < entry < stop')
    # Model adverse entry AND exit price, including changed turnover for fees.
    entry_fill = entry * (1 + slip if long else 1 - slip)
    stop_fill = stop * (1 - slip if long else 1 + slip)
    target_fill = target * (1 - slip if long else 1 + slip)
    side_in, side_out = ('buy','sell') if long else ('sell','buy')
    def scenario(qty, exit_fill):
        fi = fees(entry_fill, qty, side_in, mode)
        fo = fees(exit_fill, qty, side_out, mode)
        gross = (exit_fill-entry_fill) * qty * (1 if long else -1)
        return gross - fi['total'] - fo['total'], fi, fo
    def within(qty):
        net, fi, _ = scenario(qty, stop_fill)
        return -net <= risk and entry_fill * qty + fi['total'] <= capital
    # No leverage: gross entry notional plus charges must fit capital, including shorts.
    hi = min(1_000_000, int((capital / entry_fill).to_integral_value(rounding=ROUND_FLOOR)))
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if within(mid):
            lo = mid
        else:
            hi = mid - 1
    qty = lo
    stop_net, entry_fees, stop_fees = scenario(qty, stop_fill)
    target_net, _, target_fees = scenario(qty, target_fill)
    loss = -stop_net
    def money(v): return float(v.quantize(D('.01')))
    def serial(values): return {k: money(v) for k, v in values.items()}
    return dict(status='HYPOTHETICAL_ONLY', execution_authorized=False, executable_quantity=0,
                fee_profile=PROFILE, mode=mode, direction=direction, quantity_estimate=qty,
                capital=money(capital), risk_budget=money(risk), capital_used_estimate=money(entry_fill*qty+entry_fees['total']),
                stop_loss_estimate=money(loss), target_net_estimate=money(target_net),
                net_reward_risk=round(float(target_net/loss), 3) if loss > 0 else None,
                entry_fill_estimate=money(entry_fill), stop_fill_estimate=money(stop_fill), target_fill_estimate=money(target_fill),
                fees={'entry':serial(entry_fees), 'stop_exit':serial(stop_fees), 'target_exit':serial(target_fees)},
                binding_message=('No whole share fits both budgets' if not qty else 'Risk/capital constrained; maximum 1,000,000 shares; broker eligibility remains unverified'),
                notes=NOTES)


def context_from_frame(frame, mode, now):
    """Descriptive context only. Never promote daily bars into intraday data.
    Provider bar indices are start times, normalised to IST. VWAP is OHLCV proxy,
    NOT tick/trade VWAP; daily mode never exposes it as session VWAP.
    """
    import pandas as pd
    if mode not in ('intraday','delivery'):
        raise ValueError('Invalid mode')
    if frame is None or frame.empty:
        raise ValueError('No provider data for the requested interval')
    df = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(df.index))
    index = index.tz_localize('Asia/Kolkata') if index.tz is None else index.tz_convert('Asia/Kolkata')
    df.index = index
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise ValueError('Duplicate or unordered timestamps: context refused')
    now = pd.Timestamp(now)
    now = now.tz_localize('Asia/Kolkata') if now.tzinfo is None else now.tz_convert('Asia/Kolkata')
    required = ['Open','High','Low','Close','Volume']
    df = df[required].apply(pd.to_numeric, errors='coerce')
    if not all(math.isfinite(float(v)) for v in df.to_numpy().ravel()):
        raise ValueError('Non-finite OHLCV')
    if ((df[['Open','High','Low','Close']] <= 0).any().any() or (df.Volume < 0).any()
        or (df.High < df[['Open','Low','Close']].max(axis=1)).any()
        or (df.Low > df[['Open','High','Close']].min(axis=1)).any()):
        raise ValueError('Invalid OHLCV')
    if mode == 'intraday':
        # Reject obvious daily/coarse data masquerading as a 5-minute response.
        times = df.index
        if len(times) < 2 or not any((times[1:]-times[:-1]) == pd.Timedelta(minutes=5)):
            raise ValueError('No genuine 5-minute sequence; no daily fallback permitted')
        if any(t.second or t.microsecond or t.minute % 5 for t in times):
            raise ValueError('Unexpected 5-minute timestamp alignment')
        df = df[(df.index + pd.Timedelta(minutes=5) <= now)]
        df = df.between_time('09:15','15:25')
        if df.empty:
            raise ValueError('No completed regular-session 5-minute bars')
        day = df.index[-1].date()
        session = df[df.index.date == day]
        total = float(session.Volume.sum())
        vwap = float((((session.High+session.Low+session.Close)/3)*session.Volume).sum()/total) if total > 0 else None
        expected = pd.date_range(session.index[0], session.index[-1], freq='5min')
        gaps = len(expected.difference(session.index))
        return dict(mode=mode, interval='5m', last_close=float(session.Close.iloc[-1]),
                    first_bar=session.index[0].isoformat(), last_bar=session.index[-1].isoformat(),
                    bars=len(session), session_date=str(day), missing_slots_between_endpoints=gaps,
                    starts_at_0915=session.index[0].strftime('%H:%M') == '09:15',
                    vwap_proxy=vwap, vwap_label='Available-session 5-minute typical-price × volume proxy; not official trade VWAP',
                    minutes_since_bar_end=round((now-(session.index[-1]+pd.Timedelta(minutes=5))).total_seconds()/60, 1),
                    current_ist_date=(day == now.date()), live_quote=False, execution_authorized=False,
                    note='Historical descriptive bars, not an intraday prediction. Missing slots may represent no trades or missing data; completeness is not verified. Verify live quote, spread, current session and broker cutoff separately.')
    # Caller supplies the existing completed-session-filtered daily frame.
    df = df[df.index.date <= now.date()]
    if df.empty: raise ValueError('No completed daily bars')
    return dict(mode=mode, interval='1d', last_close=float(df.Close.iloc[-1]), last_bar=df.index[-1].isoformat(),
                bars=len(df), sma20=float(df.Close.tail(20).mean()) if len(df)>=20 else None,
                sma50=float(df.Close.tail(50).mean()) if len(df)>=50 else None,
                sma200=float(df.Close.tail(200).mean()) if len(df)>=200 else None,
                live_quote=False, execution_authorized=False,
                note='Completed-session daily trend context only; not an intraday signal or validated swing strategy. Delivery is long-only.')


def register(app, host):
    from flask import jsonify, request, send_file
    @app.get('/manual-planner')
    def manual_page():
        return send_file(Path(__file__).with_name('ManualPlanner.html'))

    @app.post('/api/manual-plan')
    def manual_plan():
        try:
            return jsonify(plan(request.get_json(silent=True)))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/manual-context/<symbol>')
    def manual_context(symbol):
        try:
            mode = request.args.get('mode', '')
            exchange = request.args.get('exchange', '').upper()
            if mode not in ('intraday','delivery') or exchange not in ('NSE','BSE'):
                raise ValueError('Select product and NSE/BSE')
            symbol = host.resolve_symbol(host.checked_symbol(symbol, exchange))
            interval = '5m' if mode == 'intraday' else '1d'
            df, source = host.DATA_MANAGER.smart_fetch(symbol, period='5d' if mode == 'intraday' else '2y',
                interval=interval, n_bars=300 if mode == 'intraday' else 500, prefer_exch=exchange, strict_exch=True)
            if mode == 'delivery' and df is not None:
                df = df[[t.date() <= host.last_completed_session(exchange=exchange) for t in df.index]]
            result = context_from_frame(df, mode, host.datetime.now(host.IST))
            result.update(symbol=symbol, exchange=exchange, source=source, input_hash=host.frame_digest(df),
                          price_adjustment=df.attrs.get("adjustment", "Provider convention; corporate actions not independently reconciled"))
            return jsonify(result)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            app.logger.warning('Manual context unavailable: %s', type(exc).__name__)
            return jsonify(error='Requested-interval context unavailable. No daily/intraday substitution was made.'), 503
