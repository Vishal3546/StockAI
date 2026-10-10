"""Download explicit NSE/BSE final bhavcopies and atomically refresh a LOCAL reference.
No credentials, web scraping bypass or remote publication. Never replaces provider
history. Optional raw file inputs make offline replay possible. Run after EOD.
"""
import argparse,hashlib,json,math,os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.reconcile_official_close import official_url,parse_official
from eod_validation import PATH
import requests

def build(session,raws):
    rows={};sources={}
    for ex,raw in raws.items():
        df=parse_official(raw,ex,session)
        if ex=='NSE':df=df[df.SctySrs.eq('EQ')]
        # Ambiguous symbol identities never silently select the first row.
        df=df[~df.TckrSymb.duplicated(keep=False)]
        sources[ex]={'url':official_url(ex,session),'sha256':hashlib.sha256(raw).hexdigest()}
        for _,r in df.iterrows():
            vals={k:float(r[v]) for k,v in dict(Open='OpnPric',High='HghPric',Low='LwPric',Close='ClsPric',Volume='TtlTradgVol').items()}
            if not all(math.isfinite(v) and v>=0 for v in vals.values()) or any(vals[k]<=0 for k in ('Open','High','Low','Close')):continue
            if not isinstance(r.ISIN,str) or not r.ISIN.startswith('IN'):continue
            rows[ex+':'+str(r.TckrSymb)]={**vals,'isin':r.ISIN}
    if set(sources)!={'NSE','BSE'}:raise ValueError('both exchange sources required for this refresh')
    return {'schema':1,'session':session,'sources':sources,'rows':rows}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--date');p.add_argument('--nse-file',type=Path);p.add_argument('--bse-file',type=Path);p.add_argument('--output',type=Path,default=PATH);a=p.parse_args()
    if not a.date:
        os.environ.setdefault('STOCKAI_OFFLINE','1');os.environ.setdefault('STOCKAI_SCAN_AUTOREFRESH','0');os.environ.setdefault('STOCKAI_AUTO_OPEN','0')
        import app as A
        a.date=str(A.last_completed_session())
    raws={}
    for ex,file in [('NSE',a.nse_file),('BSE',a.bse_file)]:
        if file:raw=file.read_bytes()
        else:
            r=requests.get(official_url(ex,a.date),headers={'User-Agent':'Mozilla/5.0'},timeout=30);r.raise_for_status();raw=r.content
        if len(raw)>30_000_000:raise ValueError('download too large')
        raws[ex]=raw
    doc=build(a.date,raws);a.output.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=a.output.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(doc,f,separators=(',',':'),allow_nan=False)
        os.replace(temp,a.output)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print(json.dumps({'session':a.date,'reference_rows':len(doc['rows']),'output':str(a.output),'sources':doc['sources']}))
if __name__=='__main__':main()
