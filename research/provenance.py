"""Content fingerprint, not a release-label assertion of research validity."""
import hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FILES = ('app.py','tv_history.py','safety.py','research/provenance.py',
         'research/data.py','research/features.py','research/ml_lab.py',
         'research/backtest.py','research/costs.py','research/run_study.py',
         'tools/build_ml_edge_study.py')
def pipeline_fingerprint():
    h=hashlib.sha256()
    for name in FILES:
        h.update(name.encode());h.update((ROOT/name).read_bytes().replace(b'\r\n', b'\n'))
    return h.hexdigest()
def frame_manifest(df):
    return {'bars':len(df),'first_session':str(df.index[0])[:10],
            'last_session':str(df.index[-1])[:10],
            'sha256':hashlib.sha256(df.to_csv(lineterminator='\n').encode()).hexdigest()}
