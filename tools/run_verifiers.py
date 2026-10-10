"""Run existing verifiers in isolated subprocesses and retain actual exit codes.
Some upstream verifiers use network data despite STOCKAI_OFFLINE; no claim of
hermetic execution. jsdom is required (npm install). A nonzero exit fails runner.
"""
import concurrent.futures, subprocess, pathlib, os, json, time, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'verification_results'; OUT.mkdir(parents=True,exist_ok=True)
def run(p):
 env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1','STOCKAI_SCAN_AUTOREFRESH':'0','STOCKAI_AUTO_OPEN':'0','PYTHONPATH':str(ROOT),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
 # This verifier intentionally toggles offline itself; inherited offline breaks its online mock phase.
 if p.name=='verify_startup_offline.py': env.pop('STOCKAI_OFFLINE',None)
 else: env['STOCKAI_OFFLINE']='1'
 t=time.time()
 try:
  r=subprocess.run(['node' if p.suffix=='.js' else sys.executable,str(p)],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
  text=r.stdout+'\n'+r.stderr; status=r.returncode
 except subprocess.TimeoutExpired as e:text=str(e.stdout)+'\n'+str(e.stderr);status='timeout'
 (OUT/(p.name+'.log')).write_text(text,encoding='utf-8')
 return {'test':p.name,'exit':status,'seconds':round(time.time()-t,2)}
if __name__=='__main__':
 files=sorted((ROOT/'tools').glob('verify_*'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  results=[]
  for r in pool.map(run,files):results.append(r);print(json.dumps(r),flush=True)
 (OUT/'summary.json').write_text(json.dumps(results,indent=2))
 sys.exit(0 if all(r['exit']==0 for r in results) else 1)
