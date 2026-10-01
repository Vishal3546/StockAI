#!/usr/bin/env python3
"""
FIX-42 · M-2 — requirements.txt → requirements.lock.json (wheel evidence)
========================================================================
`pip install -r requirements.txt` kabhi source-compile par na gire, iske liye
har pin ka PyPI par **binary wheel** hona chahiye — specifically un CPython tags
ke liye jo ye project support karta hai (3.12 / 3.13 / 3.14). Ye tool wahi
evidence PyPI se kheench kar ek committed JSON me record karta hai, taaki
`tools/verify_dependency_pins.py` offline assert kar sake (aur review me dikhe
ki claim kahaan se aaya).

Run:  python tools/build_requirements_lock.py [--requirements requirements.txt]
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / 'requirements.lock.json'
SCHEMA = 1

# Jin CPython tags ke liye wheel chahiye. Windows user (cp314) + sandbox (cp313).
CP_TAGS = ('cp312', 'cp313', 'cp314')
LINE_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-]+)\s*(?:#.*)?$')


def parse_requirements(path):
    pins, bad = [], []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#')[0].strip() if not raw.lstrip().startswith('#') else ''
        if not line:
            continue
        m = LINE_RE.match(raw)
        if m:
            pins.append((m.group(1), m.group(2)))
        else:
            bad.append(raw.strip())
    return pins, bad


def pypi(name, version):
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (StockAI lock builder)'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def classify(files):
    """Har cp tag ke liye installable wheel hai ya nahi.

    Do tarah ke "universal" wheels hote hain:
      * `py3-none-any`        → pure python, har platform
      * `py3-none-<platform>` → binary, par CPython-ABI constraint NAHI
        (xgboost isi tarah ship karta hai: win_amd64 + manylinux*). Ye bhi
        kisi bhi cp3x par install ho jaate hain, compile kuch nahi hota.
    """
    per_tag, samples, pure, universal_platforms = {}, {}, False, set()
    for f in files:
        fn = f.get('filename', '')
        if not fn.endswith('.whl'):
            continue
        if 'py3-none-any' in fn:
            pure = True
        elif '-py3-none-' in fn:
            universal_platforms.add(fn.rsplit('-', 1)[-1].replace('.whl', ''))
        for tag in CP_TAGS:
            if tag in fn:
                per_tag.setdefault(tag, []).append(fn)
    universal = pure or bool(universal_platforms)
    for tag in CP_TAGS:
        names = per_tag.get(tag, [])
        if names:
            samples[tag] = sorted(names)[0]
        elif pure:
            samples[tag] = 'py3-none-any (pure python)'
        elif universal_platforms:
            samples[tag] = 'py3-none-' + ',py3-none-'.join(sorted(universal_platforms))
    return samples, pure, universal, sorted(universal_platforms)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--requirements', type=pathlib.Path, default=ROOT / 'requirements.txt')
    ap.add_argument('--output', type=pathlib.Path, default=LOCK_PATH)
    args = ap.parse_args()

    pins, bad = parse_requirements(args.requirements)
    if not pins:
        raise SystemExit(f'no pins parsed from {args.requirements}')
    print(f'{len(pins)} pins in {args.requirements.name}; querying PyPI for wheel evidence…')

    lock = {
        'schema': SCHEMA,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'requirements_file': args.requirements.name,
        'cp_tags_required': list(CP_TAGS),
        'pins': {},
    }
    failures = []
    for name, version in pins:
        try:
            data = pypi(name, version)
        except Exception as exc:
            print(f'  ✗ {name}=={version}: PyPI query failed ({type(exc).__name__})')
            failures.append(f'{name}=={version} not on PyPI ({type(exc).__name__})')
            lock['pins'][name] = {'version': version, 'error': str(exc)}
            continue
        files = data.get('urls') or []
        wheels, pure, universal, uni_platforms = classify(files)
        sdist = any(f.get('filename', '').endswith('.tar.gz') for f in files)
        missing = [t for t in CP_TAGS if t not in wheels]
        rec = {
            'version': version,
            'requires_python': data.get('info', {}).get('requires_python'),
            'wheel_count': sum(1 for f in files if f.get('filename', '').endswith('.whl')),
            'sdist_present': sdist,
            'pure_python': pure,
            'universal_python_wheels': universal,
            'universal_platforms': uni_platforms,
            'wheel_for': wheels,
            'missing_cp_tags': missing,
        }
        lock['pins'][name] = rec
        flag = '✅' if not missing else '❌'
        print(f'  {flag} {name}=={version:<8} wheels={rec["wheel_count"]:<3} '
              f'requires_python={rec["requires_python"] or "-":<8} '
              f'{"pure-python" if pure else ",".join(sorted(wheels))}'
              + (f'  MISSING {missing}' if missing else ''))
        if missing:
            failures.append(f'{name}=={version} has no wheel for {missing} '
                            f'(pip would compile from source on those)')

    lock['unparsed_lines'] = bad
    lock['all_pins_wheel_ready'] = not failures
    lock['failures'] = failures

    tmp = args.output.with_name(args.output.name + '.tmp')
    tmp.write_text(json.dumps(lock, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(args.output)
    print(f'\n{"✅" if not failures else "❌"} {args.output}: '
          f'{len(pins) - len(failures)}/{len(pins)} pins have wheels for {", ".join(CP_TAGS)}')
    for f in failures:
        print(f'   - {f}')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
