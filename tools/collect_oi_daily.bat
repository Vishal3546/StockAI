@echo off
REM FIX-66: daily real-OI snapshot. Market close ke baad (>=15:40 IST) chalao.
REM Task Scheduler se roz apne-aap chalane ke liye is .bat ko point karo.
cd /d "%~dp0.."
python tools\collect_oi_daily.py >> reports\oi_collect.log 2>&1
