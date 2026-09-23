"""Обновление правил в README из config.json."""
import argparse
import re
from pathlib import Path

from settings import CONFIG, role_rules

ROOT = Path(__file__).resolve().parent
START = '<!-- CONFIG:START -->'
END = '<!-- CONFIG:END -->'


def configuration_section():
    r, s, p, g = (CONFIG[key] for key in ('roles', 'role_scores', 'priority', 'graph'))
    e, c = CONFIG['external_funds'], CONFIG['clusters']
    w = p['weights']
    review = p['consolidator_review_target']
    scores = {
        'coordinator': f"{s['coordinator_base']} + {s['coordinator_bridge_bonus']} × bridge_percentile при betweenness > 0; иначе {s['coordinator_base']}",
        'distributor': f"{s['distributor_base']} + {s['distributor_bonus']} × min(out_deg/{s['distributor_saturation_recipients']}, 1)",
        'consolidator': f"({s['consolidator_base']} + {s['consolidator_bonus']} × min(in_deg/{s['consolidator_saturation_payers']}, 1)) × ({s['truncated_consolidator_multiplier']} на обрыве, иначе 1)",
        'transit': f"{s['transit_base']} + {s['transit_temporal_bonus']} × temporal_share",
        'terminal': str(s['terminal']), 'peripheral': str(s['peripheral']),
    }
    rules = {role: re.sub(r'(?<=\d),(?=\d{3}\b)', ' ', rule)
             for role, rule in role_rules().items()}
    table = '\n'.join(f'| `{role}` | {rule} | `{scores[role]}` |' for role, rule in rules.items())
    return f'''{START}
## Метрики и роли

Этот раздел формируется из `config.json`: обновить — `.venv/bin/python documentation.py`, проверить соответствие — `.venv/bin/python documentation.py --check`.

Граф направленный, веса — суммы переводов. `in_deg/out_deg` — число разных плательщиков/получателей, `in_kzt/out_kzt` — наблюдаемые суммы. `seed_reach` — число различных seed, из которых узел достижим по направленному пути длиной до {g['max_hops']} переходов, без учёта самого узла. Это структурная достижимость, не доказательство происхождения денег.

PageRank использует суммы. Посредничество (`betweenness`) использует число переходов, с выборкой до {g['betweenness_samples']} исходных узлов и seed={g['random_seed']}; суммы не трактуются как расстояние. `bridge_percentile` — перцентиль посредничества среди всех узлов. `neighbor_clusters` — число кластеров непосредственных соседей, включая свой при наличии такого соседа.

Правила проверяются **сверху вниз**; первое совпадение задаёт основную роль.

| Роль | Формальное правило | Поддержка гипотезы `role_score` |
|---|---|---|
{table}

Координаторы определяются в два прохода. Базовый хаб — distributor при ≥{r['distributor_min_recipients']} получателях, иначе consolidator при ≥{r['consolidator_min_payers']} плательщиках. Сначала кандидаты проверяются по базовым хабам. Затем **все предварительные кандидаты coordinator исключаются из подтверждающих хабов**, и правило считается повторно. Так роль не возникает из взаимного подтверждения будущих координаторов. Метод консервативный: исключённый кандидат не возвращается в подтверждающие хабы, даже если сам не получил итоговую роль coordinator. Все используемые подтверждающие соседи имеют соответствующую итоговую роль. Самоперевод не подтверждает координацию.

Для входящего правила нужны консолидаторы; они могут быть seed. Для исходящего — хабы с ролью consolidator/distributor, обязательно без seed. Доля вычисляется по сумме соответствующих переводов относительно **всего входа или всего выхода в том же направлении**. Дополнительный минимум ≥{r['coordinator_min_payers']} плательщиков и ≥{r['coordinator_min_recipients']} получателей у самого координатора сохранён как аналитический порог команды; это не буквальное требование организаторов. Количество координаторов не ограничивается квотой и gid не закрепляются вручную. Посредничество влияет только на поддержку роли. Не прошедший правило узел с ≥{r['distributor_large_fanout']} получателями получает distributor.

`temporal_share` — доля исходящих операций с хотя бы одной предшествующей входящей за {CONFIG['temporal']['window_days']} дня ({CONFIG['temporal']['window_days'] * 24} часов). При датах без времени порядок внутри дня неизвестен. Суммы не сопоставляются; совпадение конкретных денег не устанавливается.

`depth={g['max_hops']}` без исходящих означает обрыв наблюдения. Такой узел не получает terminal; консолидация по входящим возможна с указанным уменьшением поддержки. Seed также не получает terminal или transit. Отношение выхода к входу для транзита применяется только при положительном входе, отсутствии обрыва и выходе не выше {r['transit_max_pass_through']} входа.

Скоры — эвристики, **не откалиброванные вероятности**. Coordinator — гипотеза структурной координации; terminal — наблюдаемое удержание за период. Они не устанавливают организатора, фактический остаток или виновность.

## Приоритет и объяснения

Перцентили считаются среди активных узлов, совпадающие значения усредняются. Нулевое значение признака получает нулевой вклад. Изоляты имеют нулевой приоритет.

```text
base_priority = {w['turnover']} × percentile(in_kzt + out_kzt)
              + {w['seed_reach']} × percentile(seed_reach)
              + {w['betweenness']} × percentile(betweenness)
              + {w['degree']} × percentile(in_deg + out_deg)
              + {w['pagerank']} × percentile(pagerank)
priority = base_priority × role_priority_multiplier × seed_priority_multiplier
```

Если исходящие ≥{format(e['min_out_kzt'], ',').replace(',', ' ')} ₸ и выход **строго больше {e['max_pass_through']} × вход**, у любой роли добавляется «Источник средств вне выборки». Нулевой вход также учитывается. Только для peripheral с этим признаком `role_priority_multiplier={p['external_peripheral_multiplier']}`; иначе он равен 1.

Для seed с ролью {', '.join(p['seed_discount_roles'])} `seed_priority_multiplier={p['seed_multiplier']}` и в why добавляется «Клиент уже известен — приоритет снижен». Seed с ролью consolidator/distributor/coordinator сохраняют множитель 1 и получают пояснение «Клиент уже известен правоохранителям, но является точкой сбора/раздачи — ключ к уровню выше». При действующих правилах seed не назначаются роли terminal, transit и coordinator; политика приоритета учитывает эти роли отдельно от классификации. Для остальных узлов множитель равен 1. `role_score` на приоритет не умножается.

Компоненты и перцентили до множителей сохраняются в `features_debug.csv`: `priority_component_*`, `priority_percentile_*`; сумма вкладов равна `base_priority_score`. Вес посредничества в приоритете — {w['betweenness']}; вес числа связей — {w['degree']}. Приоритет точки сбора без исходящих оценивается по остальным признакам. Betweenness используется для поддержки роли coordinator и сохраняется в отладочных метриках.

Приёмка проверяет среди **всех узлов** общий ранг ≤{review['max_rank_all']} для каждого узла с итоговой ролью consolidator, ≥{review['min_payers']} плательщиками и входом ≥{format(review['min_in_kzt'], ',').replace(',', ' ')} ₸. Это критерий для текущей выборки. При изменении данных фиксированные веса могут перестать выполнять его; тогда `verify.py` сообщает об ошибке.

После округления приоритета сортировка идёт по скору убывающе, при равенстве — по gid возрастающе. **Peripheral полностью исключены из топ-листа**. Выгружается до {p['top_n']} подходящих узлов. Само ограничение глубины не даёт отдельного бонуса или штрафа к приоритету.

`evidence` начинается с «Гипотеза», содержит признаки роли, количества контрагентов, суммы в тыс/млн ₸ и ограничения; длина ≤{CONFIG['output']['evidence_max_chars']} символов. При одновременном достижении порогов ≥{r['consolidator_min_payers']} плательщиков и ≥{r['distributor_min_recipients']} получателей сразу после основной гипотезы добавляется вторичный признак консолидации (для distributor/coordinator) или раздачи (для consolidator), с числом соответствующих контрагентов. Основная роль и приоритет от этой текстовой оговорки не меняются.

«Отдано дальше» — выход относительно наблюдаемого входа при сопоставимых потоках; это не доля прослеженных денег конкретного источника. `why` добавляет сравнения оборота и числа связей с активными узлами: процент узлов со **строго меньшим** значением, округлённый вниз. Числовые центральности доступны в `features_debug.csv` и `graph.json`; в evidence/why они заменены понятными объяснениями.

## Кластеры

Louvain, resolution={g['louvain_resolution']}, seed={g['random_seed']}, на ненаправленной проекции с суммированием встречных переводов. Изоляты получают отдельные кластеры. Нумерация — по размеру, при равенстве по минимальному gid. Проекция используется для сообществ и координат; роли и пути считают на направленном графе. Раскладка выполняет {g['layout_iterations']} итераций с тем же seed.

Оборот кластера — сумма оригинальных направленных внутренних рёбер, каждое учитывается один раз. Гипотеза содержит число seed, до {c['description_top_nodes']} точек сбора (consolidator/coordinator) по внутреннему входу, до {c['description_top_nodes']} распределителей (distributor/coordinator) по внутреннему выходу и наличие транзита с входом и выходом внутри кластера. Указанные gid имеют соответствующие внутренние связи. Полностью обрезанный кластер получает фразу «назначение не определить, нужна выгрузка следующего колена». При отсутствии внутренних переводов сценарий не выдумывается. `top_gids` содержит до {c['top_gids_count']} идентификаторов по приоритету. Сообщество не обязательно является единой организованной группой.
{END}'''


def check_readme():
    text = (ROOT / 'README.md').read_text()
    return text.split(START, 1)[1].split(END, 1)[0] == configuration_section().split(START, 1)[1].split(END, 1)[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'README.md'
    if args.check:
        if not check_readme():
            raise SystemExit('README/config differ; run documentation.py')
        print('README/config: PASS')
    else:
        text = path.read_text()
        before, remainder = text.split(START, 1)
        _, after = remainder.split(END, 1)
        path.write_text(before + configuration_section() + after)
        print('README updated from config.json')
