#!/usr/bin/env python3
"""
tools/verify_screener_refresh.py — FIX-79 SCREENER AUTO-REFRESH SUITE
================================================================================
  (A) CONFIG — env se on/off aur max-age parsing (garbage input par bhi).
  (B) AGE — file missing -> None, real file -> number.
  (C) AUTO-REFRESH DECISIONS — missing / purani / fresh / disabled. _spawn_scan
      stub hota hai taaki 39s ka asli scan har baar na chale.
  (D) SUBPROCESS — ASLI temp scripts se: exit 0, exit 1, timeout. Ye real code
      path hai, mock nahi.
  (E) CONCURRENCY — do scan ek saath nahi chal sakte.
  (F) ROUTES — POST-only (GET -> 405), already-running -> 409.
  (G) CACHE — scan ke baad FIX-76 ka mtime cache invalidate hota hai.
  (H) TIMESTAMP — scanner ab offset-aware likhta hai. Pehle naive local time
      likhta tha aur screener use IST maanta tha: UTC machine par exactly 5.5h
      ka galat age (measured). Purane naive format par bhi kaam karta rahe.

Chalao:  python3 tools/verify_screener_refresh.py
"""
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


import app as A  # noqa: E402
import screener as S  # noqa: E402

TMP = ROOT / '_t79'
TMP.mkdir(exist_ok=True)


def cleanup():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


# env save/restore
_ENV = {k: os.environ.get(k) for k in ('STOCKAI_SCAN_AUTOREFRESH', 'STOCKAI_SCAN_MAX_AGE_HOURS')}


def setenv(k, v):
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v


try:
    print('=' * 84)
    print(' (A) CONFIG')
    print('=' * 84)
    setenv('STOCKAI_SCAN_AUTOREFRESH', None)
    check('default auto-refresh ON', A._scan_autorefresh_on() is True)
    for v, want in (('1', True), ('0', False), ('true', True), ('false', False),
                    ('no', False), ('off', False), ('ON', True), ('  0  ', False),
                    ('', True), ('garbage', True)):
        setenv('STOCKAI_SCAN_AUTOREFRESH', v)
        check(f'AUTOREFRESH={v!r} -> {want}', A._scan_autorefresh_on() is want,
              str(A._scan_autorefresh_on()))
    setenv('STOCKAI_SCAN_AUTOREFRESH', None)

    setenv('STOCKAI_SCAN_MAX_AGE_HOURS', None)
    check('default max age 12h', A._scan_max_age_hours() == 12.0, str(A._scan_max_age_hours()))
    for v, want in (('6', 6.0), ('0.5', 0.5), ('24', 24.0)):
        setenv('STOCKAI_SCAN_MAX_AGE_HOURS', v)
        check(f'MAX_AGE={v!r} -> {want}', A._scan_max_age_hours() == want,
              str(A._scan_max_age_hours()))
    # garbage/0/negative par safe default, crash nahi
    for v in ('abc', '', '0', '-5', None):
        setenv('STOCKAI_SCAN_MAX_AGE_HOURS', v)
        got = A._scan_max_age_hours()
        check(f'MAX_AGE={v!r} -> safe positive default', got > 0, str(got))
    setenv('STOCKAI_SCAN_MAX_AGE_HOURS', None)

    print('=' * 84)
    print(' (B) AGE')
    print('=' * 84)
    orig_scan_file = A._SCAN_FILE
    A._SCAN_FILE = str(TMP / 'nope.json')
    check('missing file -> age None', A._scan_age_hours() is None)
    f = TMP / 'fake.json'
    f.write_text('{}', encoding='utf-8')
    A._SCAN_FILE = str(f)
    a = A._scan_age_hours()
    check('real file -> age number >= 0', isinstance(a, float) and a >= 0, str(a))
    check('fresh file -> age ~0h', a < 0.01, str(a))
    os.utime(f, (time.time() - 3600 * 5, time.time() - 3600 * 5))
    a5 = A._scan_age_hours()
    check('5 ghante purani file -> ~5h', 4.9 < a5 < 5.1, str(a5))
    A._SCAN_FILE = orig_scan_file

    print('=' * 84)
    print(' (C) AUTO-REFRESH DECISIONS (spawn stub hai, asli scan nahi chalta)')
    print('=' * 84)
    calls = []
    orig_spawn = A._spawn_scan
    A._spawn_scan = lambda reason: (calls.append(reason), True)[1]
    orig_file = A._SCAN_FILE
    try:
        # disabled
        setenv('STOCKAI_SCAN_AUTOREFRESH', '0')
        A._SCAN_FILE = str(TMP / 'nope.json')
        check('disabled -> scan nahi, False', A.maybe_autorefresh_scan() is False and calls == [])
        setenv('STOCKAI_SCAN_AUTOREFRESH', '1')

        # missing file
        calls.clear()
        check('missing file -> spawn', A.maybe_autorefresh_scan() is True
              and calls == ['startup-auto'], str(calls))

        # fresh file
        calls.clear()
        A._SCAN_FILE = str(f)
        os.utime(f, None)
        check('fresh file -> skip', A.maybe_autorefresh_scan() is False and calls == [])

        # purani file
        calls.clear()
        setenv('STOCKAI_SCAN_MAX_AGE_HOURS', '1')
        os.utime(f, (time.time() - 3600 * 5, time.time() - 3600 * 5))
        check('purani file (5h > 1h limit) -> spawn', A.maybe_autorefresh_scan() is True
              and calls == ['startup-auto'], str(calls))
        setenv('STOCKAI_SCAN_MAX_AGE_HOURS', None)
    finally:
        A._spawn_scan = orig_spawn
        A._SCAN_FILE = orig_file

    print('=' * 84)
    print(' (D) SUBPROCESS — asli temp scripts')
    print('=' * 84)
    ok_script = TMP / 'ok.py'
    ok_script.write_text("import json,pathlib\n"
                         "pathlib.Path('out.txt').write_text('done')\n", encoding='utf-8')
    bad_script = TMP / 'bad.py'
    bad_script.write_text("import sys\nprint('boom stderr', file=sys.stderr)\nsys.exit(3)\n",
                          encoding='utf-8')
    hang_script = TMP / 'hang.py'
    hang_script.write_text("import time\ntime.sleep(120)\n", encoding='utf-8')
    missing_script = TMP / 'does_not_exist.py'

    orig_script, orig_to = A._SCAN_SCRIPT, A._SCAN_TIMEOUT
    orig_state = dict(A._SCAN_STATE)
    try:
        # exit 0
        A._SCAN_SCRIPT = str(ok_script)
        A._SCAN_STATE.update({'running': True, 'runs': 0, 'last_ok': None})
        A._SCR_CACHE['mtime'] = 'sentinel'
        r = A._run_scan_subprocess('test-ok')
        check('exit 0 -> ok True', r is True and A._SCAN_STATE['last_ok'] is True)
        check('exit 0 -> error None', A._SCAN_STATE['last_error'] is None)
        check('exit 0 -> running False wapas', A._SCAN_STATE['running'] is False)
        check('exit 0 -> runs badha', A._SCAN_STATE['runs'] == 1, str(A._SCAN_STATE['runs']))
        check('exit 0 -> reason record hua', A._SCAN_STATE['last_reason'] == 'test-ok')
        check('exit 0 -> seconds measured >= 0', A._SCAN_STATE['last_seconds'] >= 0,
              str(A._SCAN_STATE['last_seconds']))
        check('exit 0 -> FIX-76 cache invalidate hua', A._SCR_CACHE['mtime'] is None,
              str(A._SCR_CACHE['mtime']))
        check('exit 0 -> script ne asli kaam kiya (cwd sahi tha)',
              (TMP / 'out.txt').exists())

        # exit non-zero
        A._SCAN_SCRIPT = str(bad_script)
        A._SCAN_STATE.update({'running': True})
        r = A._run_scan_subprocess('test-bad')
        check('exit 3 -> ok False', r is False and A._SCAN_STATE['last_ok'] is False)
        check('exit non-zero -> stderr error me aata hai',
              'boom stderr' in str(A._SCAN_STATE['last_error']),
              str(A._SCAN_STATE['last_error'])[:60])
        check('exit non-zero -> running False wapas', A._SCAN_STATE['running'] is False)

        # timeout
        A._SCAN_SCRIPT = str(hang_script)
        A._SCAN_TIMEOUT = 2
        A._SCAN_STATE.update({'running': True})
        t0 = time.time()
        r = A._run_scan_subprocess('test-timeout')
        el = time.time() - t0
        check('hang -> timeout par ok False', r is False)
        check('timeout message me "timeout" hai', 'timeout' in str(A._SCAN_STATE['last_error']),
              str(A._SCAN_STATE['last_error'])[:50])
        check('timeout ~2s par kata (120s wait nahi kiya)', 1.5 < el < 10, f'{el:.1f}s')
        check('timeout ke baad running False', A._SCAN_STATE['running'] is False)

        # script hi nahi hai
        A._SCAN_SCRIPT = str(missing_script)
        A._SCAN_TIMEOUT = orig_to
        A._SCAN_STATE.update({'running': True})
        r = A._run_scan_subprocess('test-missing')
        check('missing script -> ok False (crash nahi)', r is False)
        check('missing script -> error me type hai',
              A._SCAN_STATE['last_error'] and len(str(A._SCAN_STATE['last_error'])) > 0,
              str(A._SCAN_STATE['last_error'])[:60])
        check('missing script -> running False wapas', A._SCAN_STATE['running'] is False)
    finally:
        A._SCAN_SCRIPT, A._SCAN_TIMEOUT = orig_script, orig_to
        A._SCAN_STATE.clear()
        A._SCAN_STATE.update(orig_state)

    print('=' * 84)
    print(' (E) CONCURRENCY')
    print('=' * 84)
    orig_spawn2 = A._spawn_scan
    try:
        A._SCAN_STATE.update({'running': False})
        started = []
        real_thread_start = None
        # thread actually start na ho — sirf guard test karna hai
        class _FakeT:
            def __init__(self, *a, **k): pass
            def start(self): started.append(1)
        orig_thread = A.threading.Thread
        A.threading.Thread = _FakeT
        try:
            check('pehla _spawn_scan -> True', A._spawn_scan('x') is True)
            check('pehla spawn ne running True kiya', A._SCAN_STATE['running'] is True)
            check('thread start hua', len(started) == 1, str(len(started)))
            check('doosra _spawn_scan -> False (already running)', A._spawn_scan('y') is False)
            check('doosra spawn ne thread start NAHI kiya', len(started) == 1, str(len(started)))
        finally:
            A.threading.Thread = orig_thread
            A._SCAN_STATE['running'] = False
    finally:
        A._spawn_scan = orig_spawn2

    print('=' * 84)
    print(' (F) ROUTES')
    print('=' * 84)
    c = A.app.test_client()
    r = c.get('/api/screener/refresh')
    check('GET /api/screener/refresh -> 405 (POST-only)', r.status_code == 405,
          str(r.status_code))
    r = c.get('/api/screener/status')
    j = r.get_json()
    check('GET /api/screener/status -> 200', r.status_code == 200, str(r.status_code))
    check('status me saare fields hain',
          set(j['status']) >= {'running', 'last_ok', 'last_at', 'last_seconds',
                              'last_error', 'last_reason', 'runs', 'age_hours',
                              'autorefresh', 'max_age_hours', 'exists'},
          str(sorted(j['status'])))
    # /api/screener me refresh status embedded
    j2 = c.get('/api/screener').get_json()
    check('/api/screener me refresh status embedded hai', 'refresh' in j2)
    check('refresh status me autorefresh flag hai', 'autorefresh' in j2.get('refresh', {}))
    # 409 path: running True karke POST
    A._SCAN_STATE['running'] = True
    try:
        r = c.post('/api/screener/refresh')
        check('already running -> HTTP 409', r.status_code == 409, str(r.status_code))
        jj = r.get_json()
        check('409 me already_running True', jj.get('already_running') is True)
        check('409 me clear message', 'pehle se chal raha' in str(jj.get('error')),
              str(jj.get('error'))[:50])
        check('409 me bhi status milta hai', 'status' in jj)
    finally:
        A._SCAN_STATE['running'] = False

    print('=' * 84)
    print(' (H) TIMESTAMP — offset-aware (FIX-79 latent bug fix)')
    print('=' * 84)
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    aware = datetime.now(IST).isoformat()
    naive = '2026-10-03T12:27:39.808244'
    check('aware timestamp parse hota hai', S.parse_scan_ts(aware) is not None, aware[:32])
    check('aware timestamp tz-aware rehta hai', S.parse_scan_ts(aware).tzinfo is not None)
    st = S.staleness(aware)
    check('abhi likha gaya aware timestamp -> age ~0 (5.5h nahi)',
          st['age_minutes'] is not None and abs(st['age_minutes']) < 2,
          f"{st['age_minutes']} / {st['age_label']}")
    st_old = S.staleness(naive, now=datetime(2026, 10, 6, 12, 27, 39, tzinfo=IST))
    check('purana naive format abhi bhi sahi (regression nahi)',
          st_old['age_minutes'] == 4320.0, str(st_old['age_minutes']))
    nsc = (ROOT / 'nifty_scanner.py').read_text(encoding='utf-8')
    check('scanner ab datetime.now(_IST) likhta hai',
          "datetime.now(_IST).isoformat()" in nsc)
    check("scanner ab plain datetime.now().isoformat() NAHI likhta",
          "'timestamp': datetime.now().isoformat()" not in nsc)
    check('_IST scanner me defined hai', "_IST = ZoneInfo('Asia/Kolkata')" in nsc)

    print('=' * 84)
    print(' (I) WIRING + HONESTY')
    print('=' * 84)
    appsrc = (ROOT / 'app.py').read_text(encoding='utf-8')
    page = (ROOT / 'Screener.html').read_text(encoding='utf-8')
    envex = (ROOT / '.env.example').read_text(encoding='utf-8')
    blk = appsrc[appsrc.index('# FIX-79: SCREENER AUTO-REFRESH'):appsrc.index("@app.route('/')")]
    check("route POST '/api/screener/refresh'",
          "@app.route('/api/screener/refresh', methods=['POST'])" in appsrc)
    check("route '/api/screener/status'", "@app.route('/api/screener/status')" in appsrc)
    check('subprocess use hota hai (in-process import nahi)', '_sp79.run' in blk)
    check('subprocess ka cwd set hai (scan_results.json sahi jagah bane)',
          'cwd=os.path.dirname(_SCAN_SCRIPT)' in blk)
    check('subprocess timeout set hai', 'timeout=_SCAN_TIMEOUT' in blk)
    check('thread daemon hai (server band hone par atkega nahi)', 'daemon=True' in blk)
    # NOTE: pehle wala check `count('maybe_autorefresh_scan()') == 1` tha — GALAT,
    # kyunki `def maybe_autorefresh_scan():` me bhi wahi substring aata hai
    # (count 2 aata tha). Ab indented CALL ko dekhte hain.
    _call = '\n    maybe_autorefresh_scan()'
    check('maybe_autorefresh_scan sirf __main__ me call hota hai (import par nahi)',
          appsrc.count(_call) == 1
          and appsrc.index(_call) > appsrc.index("if __name__ == '__main__':"),
          f"calls={appsrc.count(_call)}")
    check('.env.example me AUTOREFRESH documented', 'STOCKAI_SCAN_AUTOREFRESH' in envex)
    check('.env.example me MAX_AGE documented', 'STOCKAI_SCAN_MAX_AGE_HOURS' in envex)
    check('.env.example abhi bhi tracked template hai', pathlib.Path(ROOT / '.env.example').exists())
    check('page par Refresh button hai', 'f_refresh' in page and '🔄 Refresh' in page)
    check('page POST karta hai (GET nahi)', "method:'POST'" in page)
    check('page poll karta hai', "'/api/screener/status'" in page)
    check('page 409 handle karta hai', 'already_running' in page)
    check('page spin animation deta hai', 'class="spin"' in page or "'spin'" in page)
    check('page button disable karta hai scan ke dauran', 'b.disabled = true' in page)
    check('page me localhost nahi', not __import__('re').search(r'localhost|127\.0\.0\.1|:5000', page))
    for label, s in (('app.py FIX-79 block', blk), ('Screener.html', page)):
        bad = [k for k in ('78% accuracy', 'win rate', 'guaranteed profit',
                           'sure shot', 'will go up') if k.lower() in s.lower()]
        check(f'{label} me fake claim nahi', not bad, str(bad))

finally:
    for k, v in _ENV.items():
        setenv(k, v)
    cleanup()

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
