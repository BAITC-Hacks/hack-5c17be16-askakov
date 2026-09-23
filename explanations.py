from settings import CONFIG


def amount(value):
    value = float(value)
    divisor, suffix = (1000000, 'млн ₸') if abs(value) >= 1000000 else (1000, 'тыс ₸') if abs(value) >= 1000 else (1, '₸')
    number = f'{value / divisor:.2f}'.rstrip('0').rstrip('.').replace('.', ',')
    return f'{number} {suffix}'


def counted(value, one, few, many):
    value = int(value)
    form = many if 11 <= value % 100 <= 14 else one if value % 10 == 1 else few if 2 <= value % 10 <= 4 else many
    return f'{value} {form}'


def describe_role(row, role):
    label = {
        'coordinator': 'координация', 'consolidator': 'признаки консолидации',
        'distributor': 'веерная раздача', 'transit': 'транзит',
        'terminal': 'конечный получатель за период', 'peripheral': 'роль не определена',
    }[role]
    prefix = f'Гипотеза: {label}.'
    mixed = (row.in_deg >= CONFIG['roles']['consolidator_min_payers']
             and row.out_deg >= CONFIG['roles']['distributor_min_recipients'])
    secondary = ''
    if mixed:
        if role == 'consolidator':
            secondary = f" Также признаки раздачи: {counted(row.out_deg, 'получатель', 'получателя', 'получателей')}."
        else:
            secondary = f" Также признаки консолидации: {counted(row.in_deg, 'плательщик', 'плательщика', 'плательщиков')}."
    flow = f" {counted(row.in_deg, 'плательщик', 'плательщика', 'плательщиков')}: {amount(row.in_kzt)}; {counted(row.out_deg, 'получатель', 'получателя', 'получателей')}: {amount(row.out_kzt)}."
    proof = ''
    if role == 'coordinator':
        if (row.incoming_consolidator_count >= CONFIG['roles']['coordinator_min_neighbors']
                and row.incoming_consolidator_share >= CONFIG['roles']['coordinator_min_flow_share']):
            proof = f" От {counted(row.incoming_consolidator_count, 'консолидатора', 'консолидаторов', 'консолидаторов')} — {row.incoming_consolidator_share:.0%} входа."
        else:
            proof = f" В {counted(row.outgoing_hub_count, 'хаб', 'хаба', 'хабов')} направлено {row.outgoing_hub_share:.0%} выхода."
    onward = ''
    if row.ratio_usable and not row.external_funds:
        onward = f' Отдано дальше {row.pass_through:.0%} наблюдаемого входа.'
    flags = ''
    if row.truncated_by_depth:
        flow = f' Плательщиков: {int(row.in_deg)}; получено {amount(row.in_kzt)}.'
        flags += f" Обрыв {CONFIG['graph']['max_hops']}-го колена: исходящие неизвестны."
    if row.external_funds:
        flags += ' Источник средств вне выборки.'
    elif row.is_seed:
        flags += ' Входящие исходного клиента неполны.'
    # Одно из чисел уже есть во вторичном признаке.
    if mixed:
        if role == 'consolidator':
            flow = f" {counted(row.in_deg, 'плательщик', 'плательщика', 'плательщиков')}: {amount(row.in_kzt)}; отдано {amount(row.out_kzt)}."
        else:
            flow = f" Получено {amount(row.in_kzt)}; {counted(row.out_deg, 'получатель', 'получателя', 'получателей')}: {amount(row.out_kzt)}."
    full = prefix + secondary + flow + proof + onward + flags
    if len(full) <= CONFIG['output']['evidence_max_chars']:
        return full
    # Сокращаем суммы и количества, сохраняя основания роли и оговорки.
    compact = f' Получено {amount(row.in_kzt)} от {int(row.in_deg)}; отдано {amount(row.out_kzt)} для {int(row.out_deg)}.'
    text = prefix + secondary + compact + proof + flags
    if len(text) > CONFIG['output']['evidence_max_chars']:
        raise ValueError('Evidence exceeds configured limit; do not silently truncate a qualification')
    return text


def describe_priority(row):
    text = (f'{row.evidence} Оборот больше, чем у {int(row.turnover_higher_than_pct)}% активных узлов; '
            f'связей больше, чем у {int(row.degree_higher_than_pct)}%. '
            f"По путям до {CONFIG['graph']['max_hops']} переходов связан с {counted(row.seed_reach, 'исходным клиентом', 'исходными клиентами', 'исходными клиентами')}.")
    if row.role_priority_multiplier < 1:
        text += f' Из-за неопределённой роли приоритет снижен на {1 - row.role_priority_multiplier:.0%}.'
    if row.is_seed:
        if row.role not in CONFIG['priority']['seed_discount_roles']:
            text += ' Клиент уже известен правоохранителям, но является точкой сбора/раздачи — ключ к уровню выше.'
        elif row.seed_priority_multiplier < 1:
            text += ' Клиент уже известен — приоритет снижен.'
    return text
