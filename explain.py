import argparse
from pathlib import Path

import pandas as pd
from csv_io import read_csv


def main():
    parser = argparse.ArgumentParser(description='Обоснование роли для одного или нескольких gid')
    parser.add_argument('gids', nargs='+')
    parser.add_argument('--out', type=Path, default=Path('output'))
    args = parser.parse_args()
    roles = read_csv(args.out / 'nodes_roles.csv')
    debug = read_csv(args.out / 'features_debug.csv')
    if roles.gid.isna().any() or debug.gid.isna().any() or set(roles.gid) != set(debug.gid):
        parser.error('Состав gid в nodes_roles.csv и features_debug.csv не совпадает')
    nodes = roles.merge(debug, on='gid', how='left', validate='one_to_one').set_index('gid')
    missing = [gid for gid in args.gids if gid not in nodes.index]
    if missing:
        parser.error('Нет в выгрузке: ' + ', '.join(missing))
    for gid in args.gids:
        row = nodes.loc[gid]
        print(f'\nКлиент {gid}: {row.role}; role_score={row.role_score:.3f}; priority={row.priority_score:.3f}')
        print('Правило:', row.role_rule)
        print('Наблюдения:', row.evidence)
        print(f'Дополнительно: колено={row.depth}; seed={row.is_seed}; обрыв={row.truncated_by_depth}; '
              f'seed_reach={row.seed_reach}; кластеров соседей={row.neighbor_clusters}; '
              f'посредничество={row.betweenness:.6f}; перцентиль={row.bridge_percentile:.3f}; '
              f'выход/вход={row.pass_through:.3f}; временная близость={row.outgoing_with_recent_incoming:.3f}.')


if __name__ == '__main__':
    main()
