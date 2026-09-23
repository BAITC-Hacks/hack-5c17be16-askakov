"""Record an audit step, rerun the pipeline and report semantic changes."""
import argparse
import difflib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'output'
EXPORTS = ['nodes_roles.csv', 'features_debug.csv', 'clusters.csv', 'top_nodes.csv']


def source_files():
    files = list(ROOT.glob('*.py')) + list(ROOT.glob('*.json')) + [ROOT / 'README.md']
    files += list((ROOT / 'web').glob('*')) + list((ROOT / 'tests').glob('*.py'))
    return [path for path in files if path.is_file()]


def read_result(folder):
    roles = pd.read_csv(folder / 'nodes_roles.csv', dtype={'gid': str})
    debug = pd.read_csv(folder / 'features_debug.csv', dtype={'gid': str})
    nodes = roles.merge(debug, on='gid', validate='one_to_one')
    top = pd.read_csv(folder / 'top_nodes.csv', dtype={'gid': str}).head(20)
    top = top.merge(nodes[['gid', 'is_seed', 'evidence']], on='gid', validate='one_to_one')
    return nodes, top


def begin(folder):
    (folder / 'before/source').mkdir(parents=True, exist_ok=False)
    for name in EXPORTS:
        shutil.copy2(OUTPUT / name, folder / 'before' / name)
    for path in source_files():
        target = folder / 'before/source' / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    print(f'Baseline saved: {folder.relative_to(ROOT)}')


def finish(folder, step, description):
    started = time.perf_counter()
    result = subprocess.run([sys.executable, str(ROOT / 'pipeline.py')], cwd=ROOT,
                            check=True, capture_output=True, text=True, timeout=300)
    elapsed = round(time.perf_counter() - started, 2)
    (folder / 'pipeline.log').write_text(result.stdout + result.stderr)
    before, previous_top = read_result(folder / 'before')
    nodes, top = read_result(OUTPUT)
    after = folder / 'after'
    after.mkdir(exist_ok=True)
    for name in EXPORTS:
        shutil.copy2(OUTPUT / name, after / name)
    roles = ['coordinator', 'consolidator', 'distributor', 'transit', 'terminal', 'peripheral']
    comparison = pd.DataFrame({'before': before.role.value_counts(), 'after': nodes.role.value_counts()}).reindex(roles).fillna(0).astype(int)
    summary = dict(step=step, changes=description, seconds=elapsed, roles=comparison.to_dict(orient='index'),
                   top20_peripheral=int((top.role == 'peripheral').sum()), top20_seed=int(top.is_seed.sum()),
                   entered=sorted(set(top.gid) - set(previous_top.gid)), left=sorted(set(previous_top.gid) - set(top.gid)),
                   top20=json.loads(top.to_json(orient='records', force_ascii=False)))
    (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    old_sources = {path.relative_to(folder / 'before/source'): path for path in (folder / 'before/source').rglob('*') if path.is_file()}
    new_sources = {path.relative_to(ROOT): path for path in source_files()}
    diff = []
    for name in sorted(set(old_sources) | set(new_sources)):
        old = old_sources[name].read_text() if name in old_sources else ''
        new = new_sources[name].read_text() if name in new_sources else ''
        diff.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=f'before/{name}', tofile=f'after/{name}'))
    (folder / 'changes.diff').write_text(''.join(diff))
    lines = [f'# Пункт {step}', '', description, '', f'Полный запуск: {elapsed} с.', '', '| Роль | До | После |', '|---|---:|---:|']
    lines += [f'| {role} | {row.before} | {row.after} |' for role, row in comparison.iterrows()]
    lines += ['', f"Топ-20: peripheral — {summary['top20_peripheral']}; seed — {summary['top20_seed']}.", '', '| № | gid | Роль | Приоритет | Seed | why полностью |', '|---:|---|---|---:|---|---|']
    for row in top.itertuples(index=False):
        lines.append(f'| {row.rank} | {row.gid} | {row.role} | {row.priority_score:.6f} | {"да" if row.is_seed else "нет"} | {row.why} |')
    lines += ['', '## Топ-5: полные evidence и why', '']
    for row in top.head(5).itertuples(index=False):
        lines += [f'### {row.rank}. {row.gid}', '', f'evidence: {row.evidence}', '', f'why: {row.why}', '']
    (folder / 'report.md').write_text('\n'.join(lines))
    print(f'ПУНКТ {step}: {elapsed} с; {description}')
    print(comparison.to_string())
    print(top[['rank', 'gid', 'role', 'priority_score', 'is_seed']].to_string(index=False))
    print(f"Топ-20: peripheral={summary['top20_peripheral']}, seed={summary['top20_seed']}; вошли={summary['entered']}; вышли={summary['left']}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['begin', 'finish'])
    parser.add_argument('step', type=int)
    parser.add_argument('--changes', default='')
    args = parser.parse_args()
    folder = OUTPUT / 'audit' / f'step{args.step}'
    if args.action == 'begin':
        begin(folder)
    else:
        finish(folder, args.step, args.changes)
