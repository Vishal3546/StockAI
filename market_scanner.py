"""FIX109 indexed-universe browsing + resumable, bounded full-market daily scans.
SQLite snapshots; one cross-process worker lease; no orders or ML batch training.
Listing coverage is source-dependent, not a claim of all currently tradable shares.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCOPES=('ALL','NSE','BSE')
STATES=('PENDING','OK','HISTORICAL_ONLY','INSUFFICIENT_HISTORY','NO_RECENT_VOLUME','ERROR')
SORTS={'symbol':'symbol','sector':'sector','price':'price','change_pct':'change_pct',
       'rsi':'rsi','vol_ratio':'vol_ratio','signal_score':'score','ensemble':'score'}

def now(): return datetime.now(timezone.utc).isoformat()

class Busy(Exception): pass

class Lease:
    """Nonblocking local-host process lease. Released by OS on crash."""
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.f=open(path,'a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self.f.seek(0,2)
                if not self.f.tell():self.f.write(b'0');self.f.flush()
                self.f.seek(0);msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.f.close();raise Busy('Another scanner worker owns the local process lease') from None
    def close(self):
        if self.f.closed:return
        try:
            if os.name=='nt':
                import msvcrt
                self.f.seek(0);msvcrt.locking(self.f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(),fcntl.LOCK_UN)
        finally:self.f.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def listing_analysis(item):
    import nifty_scanner as scanner
    from daily_context import fetch
    df=fetch(item['symbol'],'2y',item['exchange'])
    if len(df)<200:
        return dict(symbol=item['symbol'],exchange=item['exchange'],sector=item['sector'],
            price=float(df.Close.iloc[-1]),source=df.attrs.get('source'),data_as_of=df.attrs.get('as_of'),
            historical_only=bool(df.attrs.get('historical_only')),input_hash=df.attrs.get('input_hash'),
            signal='UNRATED',signal_score=None,analysis_status='INSUFFICIENT_HISTORY',
            signal_basis=f'{len(df)} completed bars; 200 required for technical score',execution_authorized=False)
    result=scanner.analyze_frame(df,item['symbol'],item['exchange'],df.attrs.get('source'))
    result['sector']=item['sector']
    result['analysis_status']='HISTORICAL_ONLY' if result['historical_only'] else 'OK'
    if not result['historical_only'] and float(df.Volume.iloc[-1])==0:
        result.update(analysis_status='NO_RECENT_VOLUME',signal='UNRATED',signal_score=None,ensemble=None,
            signal_basis='Latest reported session has zero volume; liquidity/trading eligibility unverified')
    return result

class MarketScanner:
    def __init__(self,host,path=None,analyze=listing_analysis,delay=.5,budget=1800):
        self.host=host;self.path=Path(path or Path(__file__).with_name('.stockai')/'market_scanner.sqlite')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lease_path=self.path.with_suffix('.lock');self.analyze=analyze
        self.delay=delay;self.budget=budget;self._sync_lock=threading.Lock();self._digest=None
        try:self.bse_generated=json.loads(Path(host.BSE_MASTER_FILE).read_text()).get('generated_at','unknown')
        except (AttributeError,OSError,ValueError):self.bse_generated='unknown'
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''CREATE TABLE IF NOT EXISTS listings (
              exchange TEXT NOT NULL,symbol TEXT NOT NULL,name TEXT,sector TEXT,active INTEGER DEFAULT 1,
              status TEXT DEFAULT 'PENDING',error TEXT,result TEXT,price REAL,change_pct REAL,rsi REAL,
              vol_ratio REAL,score REAL,signal TEXT DEFAULT 'UNRATED',asof TEXT,updated TEXT,
              PRIMARY KEY(exchange,symbol));
              CREATE TABLE IF NOT EXISTS job (id INTEGER PRIMARY KEY CHECK(id=1),state TEXT,scope TEXT,
              started TEXT,updated TEXT,targets TEXT,master_digest TEXT,pause INTEGER DEFAULT 0,reason TEXT);
              CREATE TABLE IF NOT EXISTS queue (ordinal INTEGER PRIMARY KEY,exchange TEXT,symbol TEXT,done INTEGER DEFAULT 0);
              CREATE INDEX IF NOT EXISTS active_exchange ON listings(active,exchange);
              CREATE INDEX IF NOT EXISTS queue_done ON queue(done,ordinal);''')
        self.recover()
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:
            yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def recover(self):
        try:lease=Lease(self.lease_path)
        except Busy:return
        try:
            with self.db() as c:c.execute("UPDATE job SET state='PAUSED',reason='Previous worker stopped; resume explicitly',updated=? WHERE state='RUNNING'",(now(),))
        finally:lease.close()
    def targets(self):
        return {e:str(self.host.last_completed_session(exchange=e)) for e in ('NSE','BSE')}
    def sync(self):
        # Freeze current master snapshot; never accept Yahoo's unverified direct-entry fallback.
        rows={}
        for r in list(self.host.DYNAMIC_STOCK_DB):
            e=str(r.get('ex') or '').upper();s=str(r.get('sym') or '').upper().strip()
            if e in ('NSE','BSE') and re.fullmatch(r'[A-Z0-9&_.-]{1,40}',s):
                rows[(e,s)]=(e,s,str(r.get('name') or s),str(r.get('sec') or 'Unknown'))
        ordered=sorted(rows.values())
        digest=hashlib.sha256(json.dumps(ordered,ensure_ascii=False).encode()).hexdigest()
        with self._sync_lock:
            if digest!=self._digest:
                with self.db() as c:
                    c.execute('UPDATE listings SET active=0')
                    c.executemany('''INSERT INTO listings(exchange,symbol,name,sector,active) VALUES(?,?,?,?,1)
                       ON CONFLICT(exchange,symbol) DO UPDATE SET name=excluded.name,sector=excluded.sector,active=1''',ordered)
                self._digest=digest
        # Re-evaluate freshness on reads too, not only when the scan ran.
        with self.db() as c:
            for exchange,target in self.targets().items():
                c.execute("UPDATE listings SET status='HISTORICAL_ONLY',score=NULL,signal='UNRATED' WHERE exchange=? AND asof<? AND status='OK'",(exchange,target))
        return digest
    def coverage(self):
        rows=list(self.host.DYNAMIC_STOCK_DB)
        nse={str(r.get('sym','')).upper() for r in rows if str(r.get('ex') or '').upper()=='NSE'}
        bse={str(r.get('sym','')).upper() for r in rows if str(r.get('ex') or '').upper()=='BSE'}
        try:fallback={str(r.get('sym','')).upper() for r in self.host._fallback_master_list()}
        except AttributeError:fallback=set()
        incomplete=not nse or bool(fallback and nse.issubset(fallback))
        return dict(nse_indexed=len(nse),bse_indexed=len(bse),nse_fallback=incomplete,
                    bse_master_generated=self.bse_generated)
    def status(self):
        self.recover()
        self.sync()
        with self.db() as c:
            c.execute('BEGIN')
            row=c.execute('SELECT * FROM job WHERE id=1').fetchone()
            if not row:return dict(state='IDLE',running=False,total=0,processed=0,remaining=0)
            st=dict(row);total,done=c.execute('SELECT count(*),coalesce(sum(done),0) FROM queue').fetchone()
            errors=c.execute("SELECT count(*) FROM queue q JOIN listings l USING(exchange,symbol) WHERE q.done=1 AND l.status='ERROR'").fetchone()[0]
        st.update(session_changed=json.loads(st['targets'])!=self.targets(),master_changed=st['master_digest']!=self._digest,running=st['state']=='RUNNING',total=total,processed=done,remaining=total-done,errors=errors,targets=json.loads(st['targets']))
        return st
    def start(self,scope='ALL',resume=False,background=True):
        if scope not in SCOPES:raise ValueError('scope must be ALL, NSE or BSE')
        self.sync()
        if scope in ('ALL','NSE') and self.coverage()['nse_fallback']:
            raise ValueError('Full NSE master not loaded; current NSE entries are fallback only. Wait for the existing master refresh/restart online; BSE browsing remains available.')
        # Same order as legacy cohort launcher: host guard, then process lease.
        with self.host._scan_lock:
            if self.host._SCAN_STATE['running']:raise Busy('Research-cohort scan is still running')
            lease=Lease(self.lease_path)
            try:
                with self.db() as c:
                    if resume:
                        job=c.execute('SELECT * FROM job WHERE id=1').fetchone()
                        if not job or job['state'] not in ('PAUSED','EXPIRED','ERROR'):raise ValueError('No paused scan to resume')
                        if job['scope']!=scope:raise ValueError('Select the paused scan scope before resuming')
                        if json.loads(job['targets'])!=self.targets():raise ValueError('Completed market session changed; start a new scan instead')
                        c.execute("UPDATE job SET state='RUNNING',pause=0,reason=NULL,updated=? WHERE id=1",(now(),))
                    else:
                        c.execute('DELETE FROM queue')
                        sql='SELECT exchange,symbol FROM listings WHERE active=1'+(' AND exchange=?' if scope!='ALL' else '')+' ORDER BY exchange,symbol'
                        keys=c.execute(sql,() if scope=='ALL' else (scope,)).fetchall()
                        if not keys:raise ValueError('No indexed listings in this scope')
                        c.executemany('INSERT INTO queue(ordinal,exchange,symbol) VALUES(?,?,?)',[(i,r['exchange'],r['symbol']) for i,r in enumerate(keys)])
                        c.execute("UPDATE listings SET status='PENDING',result=NULL,error=NULL,price=NULL,change_pct=NULL,rsi=NULL,vol_ratio=NULL,score=NULL,signal='UNRATED',asof=NULL,updated=NULL WHERE (exchange,symbol) IN (SELECT exchange,symbol FROM queue)")
                        c.execute('INSERT OR REPLACE INTO job VALUES(1,?,?,?,?,?,?,0,NULL)',('RUNNING',scope,now(),now(),json.dumps(self.targets()),self._digest))
                if background:
                    thread=threading.Thread(target=self.work,args=(lease,),daemon=True,name='market-scanner');thread.start()
                else:self.work(lease)
            except BaseException:lease.close();raise
        return {'started':True}
    def pause(self):
        with self.db() as c:c.execute('UPDATE job SET pause=1 WHERE state=\'RUNNING\'')
    def work(self,lease):
        started=time.monotonic();streak=0;state='COMPLETED';reason=None
        try:
            while True:
                with self.db() as c:
                    job=c.execute('SELECT * FROM job WHERE id=1').fetchone()
                    item=c.execute('SELECT l.* FROM queue q JOIN listings l USING(exchange,symbol) WHERE q.done=0 ORDER BY ordinal LIMIT 1').fetchone()
                if item is None:break
                if job['pause'] or time.monotonic()-started>=self.budget:
                    state='PAUSED';reason='Paused after current request; Resume continues remaining listings';break
                if json.loads(job['targets'])!=self.targets():
                    state='EXPIRED';reason='Completed session changed; start a new scan';break
                item=dict(item)
                try:
                    result=self.analyze(item)
                    if not isinstance(result,dict) or result.get('symbol')!=item['symbol'] or result.get('exchange')!=item['exchange']:
                        raise ValueError('Analyzer returned a mismatched listing')
                    if result.get('data_as_of') and result['data_as_of']>json.loads(job['targets'])[item['exchange']]:
                        state='EXPIRED';reason='New-session result discarded; restart scan';break
                    from nifty_scanner import _nan_safe
                    result=_nan_safe(result);encoded=json.dumps(result,allow_nan=False)
                    status=result.get('analysis_status','OK')
                    if status not in ('OK','HISTORICAL_ONLY','INSUFFICIENT_HISTORY','NO_RECENT_VOLUME'):raise ValueError('Invalid analysis status')
                    if status=='HISTORICAL_ONLY':
                        result['signal_score']=None;result['ensemble']=None;encoded=json.dumps(result,allow_nan=False)
                    error=None;streak=0
                except Exception as exc:
                    result={};encoded=None;status='ERROR';error=f'{type(exc).__name__}: {str(exc)[:240]}';streak+=1
                with self.db() as c:
                    c.execute('''UPDATE listings SET status=?,error=?,result=?,price=?,change_pct=?,rsi=?,vol_ratio=?,score=?,signal=?,asof=?,updated=?
                       WHERE exchange=? AND symbol=?''',(status,error,encoded,result.get('price'),result.get('change_pct'),result.get('rsi'),result.get('vol_ratio'),result.get('signal_score'),result.get('signal','UNRATED'),result.get('data_as_of'),now(),item['exchange'],item['symbol']))
                    c.execute('UPDATE queue SET done=1 WHERE exchange=? AND symbol=?',(item['exchange'],item['symbol']))
                    c.execute('UPDATE job SET updated=? WHERE id=1',(now(),))
                if streak>=10:
                    state='PAUSED';reason='10 consecutive failures; provider/instrument checks needed before resume';break
                time.sleep(self.delay)
        except Exception as exc:state='ERROR';reason=f'{type(exc).__name__}: {str(exc)[:240]}'
        except BaseException as exc:
            state='ERROR';reason='Worker interrupted: '+type(exc).__name__
            raise
        finally:
            try:
                with self.db() as c:c.execute('UPDATE job SET state=?,reason=?,updated=? WHERE id=1',(state,reason,now()))
            finally:lease.close()
    def page(self,args):
        self.sync();scope=args.get('scope','ALL')
        if scope not in SCOPES:raise ValueError('Invalid scope')
        try:limit=int(args.get('limit',50));offset=int(args.get('offset',0))
        except (TypeError,ValueError):raise ValueError('Invalid pagination') from None
        if not 1<=limit<=100 or not 0<=offset<=100000:raise ValueError('Pagination outside limits')
        where=['active=1'];params=[]
        if scope!='ALL':where.append('exchange=?');params.append(scope)
        base=' AND '.join(where);baseparams=list(params)
        q=str(args.get('q','')).strip().upper()
        if len(q)>80:raise ValueError('Search is too long')
        if q:
            escaped=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            where.append("(symbol LIKE ? ESCAPE '\\' OR upper(name) LIKE ? ESCAPE '\\')");params.extend([escaped+'%','%'+escaped+'%'])
        for field in ('sector','signal'):
            if args.get(field):where.append(field+'=?');params.append(args[field])
        for arg,column in (('min_score','score'),('min_vol','vol_ratio')):
            if args.get(arg) not in (None,''):
                import math
                try:v=float(args[arg])
                except (ValueError,TypeError):raise ValueError('Invalid numeric filter') from None
                if not math.isfinite(v):raise ValueError('Invalid numeric filter')
                where.append(column+'>=?');params.append(v)
        sort_key=args.get('sort') if args.get('sort') in SORTS else 'symbol'
        sort=SORTS[sort_key];order='DESC' if args.get('order')=='desc' else 'ASC'
        with self.db() as c:
            c.execute('BEGIN')
            total=c.execute('SELECT count(*) FROM listings WHERE '+base,baseparams).fetchone()[0]
            matched=c.execute('SELECT count(*) FROM listings WHERE '+' AND '.join(where),params).fetchone()[0]
            offset=min(offset,(max(matched-1,0)//limit)*limit)
            facets={key:{r[0]:r[1] for r in c.execute(f'SELECT {col},count(*) FROM listings WHERE {base} GROUP BY {col}',baseparams)} for key,col in (('sectors','sector'),('signals','signal'))}
            records=c.execute('SELECT * FROM listings WHERE '+' AND '.join(where)+f' ORDER BY ({sort} IS NULL), {sort} {order},exchange,symbol LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            states=dict(c.execute('SELECT status,count(*) FROM listings WHERE '+base+' GROUP BY status',baseparams).fetchall())
        rows=[]
        for r in records:
            row=json.loads(r['result']) if r['result'] else dict(price=None,signal='UNRATED',signal_score=None,source=None)
            row.update(symbol=r['symbol'],exchange=r['exchange'],name=r['name'],sector=r['sector'],analysis_status=r['status'],analysis_error=r['error'],checked_at=r['updated'],signal=r['signal'],signal_score=r['score'])
            if r['status']=='HISTORICAL_ONLY':
                row.update(historical_only=True,ensemble=None,signal_basis='Earlier completed session; current-session rank withheld')
            rows.append(row)
        return dict(ok=True,rows=rows,total=total,matched=matched,facets=facets,offset=offset,limit=limit,states=states,
            sort={'key':sort_key,'order':order.lower()},scope=scope,
            master_digest=self._digest,coverage=self.coverage(),master_note='Existing NSE/BSE indexed listings, not guaranteed complete/active/tradable. Dual listings are separate. BSE master is a dated provider artifact; no master prices are used as current quotes.')

def register(app,host):
    from flask import jsonify,request
    service=MarketScanner(host);host.MARKET_SCANNER=service
    @app.get('/api/screener/universe')
    def market_page():
        try:return jsonify(service.page(request.args))
        except ValueError as exc:return jsonify(ok=False,error=str(exc)),400
        except sqlite3.Error:return jsonify(ok=False,error='Scanner storage busy/unavailable; retry later'),503
    @app.get('/api/screener/universe/status')
    def market_status():return jsonify(service.status())
    @app.post('/api/screener/universe/scan')
    def market_command():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):return jsonify(error='JSON object required'),400
        try:
            action=body.get('action');scope=body.get('scope','ALL')
            if action=='pause':service.pause();return jsonify(ok=True,note='Pause requested; current provider request must finish')
            if action not in ('start','resume'):raise ValueError('Invalid action')
            service.start(scope,resume=action=='resume')
            return jsonify(ok=True),202
        except Busy as exc:return jsonify(error=str(exc)),409
        except ValueError as exc:return jsonify(error=str(exc)),400
