from explanations import amount
from settings import CONFIG


def cluster_hypothesis(subset, graph, members):
    n_seed = int(subset.is_seed.sum())
    prefix = f'Гипотеза: исходных клиентов (seed) — {n_seed}. '
    if subset.truncated_by_depth.all():
        return prefix + 'назначение не определить, нужна выгрузка следующего колена.'
    incoming, outgoing = dict.fromkeys(members, 0.), dict.fromkeys(members, 0.)
    for src in members:
        for dst, attrs in graph[src].items():
            if dst in members:
                incoming[dst] += attrs['sum_kzt']
                outgoing[src] += attrs['sum_kzt']
    if not any(outgoing.values()):
        return prefix + 'Внутри кластера переводов нет; назначение не определить по доступным связям.'
    roles = subset.set_index('gid').role.to_dict()
    limit = CONFIG['clusters']['description_top_nodes']

    def leaders(accepted, flow, both=False):
        ids = [gid for gid in members if roles[gid] in accepted and flow[gid] > 0
               and (not both or (incoming[gid] > 0 and outgoing[gid] > 0))]
        ids.sort(key=lambda gid: (-flow[gid], int(gid)))
        return ', '.join(f'{gid} ({amount(flow[gid])})' for gid in ids[:limit])

    gathering = leaders({'consolidator', 'coordinator'}, incoming)
    distribution = leaders({'distributor', 'coordinator'}, outgoing)
    transit = leaders({'transit'}, outgoing, both=True)
    parts = [f'Поступления сходятся в {gathering}.' if gathering else 'Точки сбора внутри кластера не выявлены.',
             f'Распределение через {distribution}.' if distribution else 'Распределители внутри кластера не выявлены.',
             f'Есть транзит через {transit}.' if transit else 'Транзит внутри кластера не выявлен.']
    if subset.truncated_by_depth.any():
        parts.append(f'У {int(subset.truncated_by_depth.sum())} узлов исходящие за границей выгрузки неизвестны.')
    return prefix + ' '.join(parts)
