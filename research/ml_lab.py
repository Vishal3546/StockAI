"""
research/ml_lab.py — honest ML evaluation (purged walk-forward + permutation null)
================================================================================
Kyun yeh file hai: app.py ka current ML "accuracy" ek SINGLE 80/20 split se
aata hai. Uska 95% noise band ±19pp hai (measured), matlab wo number kuch bhi
bata sakta hai. Yahan do cheezein add ki gayi hain:

  1. PURGED WALK-FORWARD — train kabhi test ke aage nahi jaata, aur train/test
     ke beech `horizon` bars ka EMBARGO hota hai (warna overlapping labels
     leakage banate hain).
  2. PERMUTATION NULL — train labels shuffle karke wahi pipeline dobara chalai
     jaati hai. Jo accuracy shuffle ke baad bhi milti hai, wahi "no-skill"
     floor hai. Isse pata chalta hai ki edge asli hai ya shor.

Saath me: baseline (majority class), per-fold dispersion, aur binomial CI.
"""
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd


def _default_model():
    from sklearn.ensemble import GradientBoostingClassifier
    return GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
                                      subsample=0.8, random_state=42)


@dataclass
class WFResult:
    oos_index: pd.DatetimeIndex
    oos_prob: np.ndarray
    oos_y: np.ndarray
    folds: list
    accuracy: float
    baseline: float
    edge_pp: float
    fold_sigma_pp: float
    ci95_pp: float
    n_oos: int

    def to_dict(self) -> dict:
        return {
            'accuracy_pct': round(self.accuracy * 100, 2),
            'baseline_pct': round(self.baseline * 100, 2),
            'edge_pp': round(self.edge_pp, 2),
            'fold_sigma_pp': round(self.fold_sigma_pp, 2),
            'ci95_pp': round(self.ci95_pp, 2),
            'n_oos': self.n_oos,
            'folds': self.folds,
        }


def purged_walk_forward(X: pd.DataFrame, y: pd.Series, n_folds: int = 5,
                        warmup: int = 252, embargo: int = 0,
                        model_fn: Optional[Callable] = None,
                        shuffle_train_labels: bool = False,
                        seed: int = 0) -> WFResult:
    """
    Expanding-window walk-forward:
        fold k → test = [start_k, start_k + block)  ·  train = [0, start_k - embargo)
    `shuffle_train_labels=True` → permutation null (train labels shuffled, test untouched).
    """
    from sklearn.preprocessing import StandardScaler

    model_fn = model_fn or _default_model
    # NOTE: `inf` ko 1.8e308 (nan_to_num ka default) banane se StandardScaler
    # NaN produce karta hai — isliye inf ko 0 par map karte hain aur extreme
    # values clip karte hain. (Ye bug study ke pehle run me pakda gaya tha.)
    Xv = np.nan_to_num(X.values.astype(float), nan=0.0, posinf=0.0, neginf=0.0)
    Xv = np.clip(Xv, -1e12, 1e12)
    yv = y.values.astype(float)
    idx = X.index

    n = len(Xv)
    usable = n - warmup
    if usable < n_folds * 10:
        raise ValueError(f"not enough data: n={n}, warmup={warmup}, folds={n_folds}")
    block = usable // n_folds

    oos_idx, oos_p, oos_y, folds = [], [], [], []
    rng = np.random.default_rng(seed)

    for k in range(n_folds):
        start = warmup + k * block
        end = min(start + block, n)
        train_end = start - embargo                 # ← purge/embargo
        if train_end < 150 or end - start < 10:
            continue
        Xtr, ytr = Xv[:train_end], yv[:train_end].copy()
        Xte, yte = Xv[start:end], yv[start:end]
        if shuffle_train_labels:                    # permutation null
            ytr = rng.permutation(ytr)

        sc = StandardScaler().fit(Xtr)
        mdl = model_fn()
        mdl.fit(sc.transform(Xtr), ytr)
        p = mdl.predict_proba(sc.transform(Xte))[:, 1]

        oos_idx.append(idx[start:end]); oos_p.append(p); oos_y.append(yte)
        acc = float(((p >= 0.5).astype(int) == yte).mean())
        base = float(max(yte.mean(), 1 - yte.mean()))
        folds.append({'fold': k + 1, 'train_n': int(train_end), 'test_n': int(end - start),
                      'test_start': str(idx[start])[:10], 'test_end': str(idx[end - 1])[:10],
                      'accuracy_pct': round(acc * 100, 2), 'baseline_pct': round(base * 100, 2),
                      'edge_pp': round((acc - base) * 100, 2)})

    if not oos_p:
        raise ValueError("no valid folds produced")

    oos_p = np.concatenate(oos_p); oos_y = np.concatenate(oos_y)
    oos_idx = pd.DatetimeIndex(np.concatenate([np.asarray(i) for i in oos_idx]))

    acc = float(((oos_p >= 0.5).astype(int) == oos_y).mean())
    base = float(max(oos_y.mean(), 1 - oos_y.mean()))
    fold_accs = np.array([f['accuracy_pct'] for f in folds]) / 100.0
    se = float(np.sqrt(max(acc * (1 - acc), 1e-9) / len(oos_y)))   # binomial SE

    return WFResult(oos_index=oos_idx, oos_prob=oos_p, oos_y=oos_y, folds=folds,
                    accuracy=acc, baseline=base, edge_pp=(acc - base) * 100,
                    fold_sigma_pp=float(fold_accs.std() * 100), ci95_pp=se * 1.96 * 100,
                    n_oos=len(oos_y))


def permutation_null(X, y, n_perm: int = 3, **kw) -> dict:
    """Shuffled-label null distribution — isse pata chalta hai 'kitna edge noise hai'."""
    nulls = []
    for s in range(n_perm):
        try:
            r = purged_walk_forward(X, y, shuffle_train_labels=True, seed=s, **kw)
            nulls.append(r.accuracy * 100)
        except Exception:
            continue
    if not nulls:
        return {'n': 0}
    return {'n': len(nulls), 'mean_pct': round(float(np.mean(nulls)), 2),
            'min_pct': round(float(np.min(nulls)), 2), 'max_pct': round(float(np.max(nulls)), 2),
            'values': [round(v, 2) for v in nulls]}


def verdict(real: WFResult, null: Optional[dict] = None) -> str:
    """Plain-Hinglish verdict — UI/report me dikhane ke liye."""
    e = real.edge_pp
    lo = real.accuracy * 100 - real.ci95_pp
    if null and null.get('n'):
        floor = null['max_pct']
        if real.accuracy * 100 <= floor:
            return f'NO EDGE — accuracy {real.accuracy*100:.1f}% shuffled-label ceiling {floor:.1f}% se neeche/barabar hai'
    if e > 5 and lo > real.baseline * 100:
        return f'POSSIBLE EDGE (+{e:.1f}pp) — verify on more data before trusting'
    if e > 0:
        return f'MARGINAL ({e:+.1f}pp) — noise band (±{real.ci95_pp:.1f}pp) ke andar'
    return f'NO EDGE ({e:+.1f}pp vs baseline)'
