"""Same-observation validation and reproducibility; no trading permission."""
import hashlib
import importlib.metadata
import json
import platform
from collections.abc import Mapping
import numpy as np


def fold_measure(y_train, y_test, prediction, *, first_session, last_session):
    train, test, pred = map(np.asarray, (y_train, y_test, prediction))
    if not len(train) or not len(test) or test.shape != pred.shape:
        raise ValueError('empty/misaligned fold')
    if not all(np.isin(a, [0,1]).all() for a in (train,test,pred)):
        raise ValueError('binary labels required')
    majority = int(train.mean() >= .5)
    return dict(n=len(test), correct=int((test==pred).sum()),
                baseline_correct=int((test==majority).sum()),
                training_majority=majority, first_session=str(first_session),
                last_session=str(last_session))


def summarize(folds):
    if not folds:
        return dict(accuracy=None, baseline=None, edge=None, fold_std_pp=None, n_oos=0, folds=[])
    n=sum(f['n'] for f in folds)
    acc=100*sum(f['correct'] for f in folds)/n
    base=100*sum(f['baseline_correct'] for f in folds)/n
    rates=[100*f['correct']/f['n'] for f in folds]
    pn=sum(f.get('policy_n',0) for f in folds)
    return dict(policy_n=pn, policy_coverage_pct=round(100*pn/n,1),
                policy_accuracy=round(100*sum(f.get('policy_correct',0) for f in folds)/pn,1) if pn else None,
                policy_baseline=round(100*sum(f.get('policy_baseline_correct',0) for f in folds)/pn,1) if pn else None,
                accuracy=round(acc,1),baseline=round(base,1),edge=round(acc-base,1),
                fold_std_pp=round(float(np.std(rates,ddof=1)),2) if len(rates)>1 else None,
                n_oos=n,folds=folds)


def ensemble_predict(X_train, y_train, X_test, config):
    """Same classifiers, parameters and weights as live final-split prediction.

    Optional XGBoost absent -> explicit three-model scope. Fit errors are NOT
    silently converted to a good fold or dropped difficult observations.
    """
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    constructors=[('gradient_boosting',GradientBoostingClassifier,'ML_GB_PARAMS'),
                  ('random_forest',RandomForestClassifier,'ML_RF_PARAMS'),
                  ('logistic_regression',LogisticRegression,'ML_LR_PARAMS')]
    try:
        from xgboost import XGBClassifier
    except ImportError:
        pass
    else:
        constructors.append(('xgboost',XGBClassifier,'ML_XGB_PARAMS'))
    sc=StandardScaler();train=sc.fit_transform(X_train);test=sc.transform(X_test)
    probabilities=[]
    for name,cls,key in constructors:
        params = config[key]
        if not isinstance(params, Mapping):raise TypeError('model parameters must be a mapping')
        model=cls(**params);model.fit(train,y_train)
        probabilities.append(model.predict_proba(test)[:,list(model.classes_).index(1)])
    weights=np.asarray(config['ML_WEIGHTS_4' if len(constructors)==4 else 'ML_WEIGHTS_3'],dtype=float)
    if len(weights)!=len(probabilities) or not np.isfinite(weights).all() or (weights<0).any() or weights.sum()<=0:
        raise ValueError('invalid ensemble weights')
    return np.average(probabilities,axis=0,weights=weights),[x[0] for x in constructors]


def reproducibility(frame, config, pipeline_hash):
    versions={}
    for pkg in ('numpy','pandas','scikit-learn','xgboost'):
        try:versions[pkg]=importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:versions[pkg]=None
    raw=frame[['Open','High','Low','Close','Volume']].to_csv(lineterminator='\n').encode()
    params={k:v for k,v in config.items() if k.startswith('ML_')}
    return dict(input_sha256=hashlib.sha256(raw).hexdigest(),bars=len(frame),
                first_session=str(frame.index[0]),last_session=str(frame.index[-1]),
                model_parameters=params,config_sha256=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest(),
                pipeline_sha256=pipeline_hash,versions=versions,python=platform.python_version(),platform=platform.platform(),
                scope='Compare input bytes, config, model availability and dependency versions before expecting identical predictions. Not a bitwise cross-platform guarantee.')
