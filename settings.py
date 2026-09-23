"""Shared analytical parameters and descriptions from one versioned config."""
import json
from pathlib import Path

CONFIG = json.loads(Path(__file__).with_name('config.json').read_text())


def role_rules():
    r = CONFIG['roles']
    return {
        'coordinator': f"Не seed; ≥{r['coordinator_min_payers']} плательщиков и ≥{r['coordinator_min_recipients']} получателей; вход от ≥{r['coordinator_min_neighbors']} консолидаторов ≥{r['coordinator_min_flow_share']:.0%} входа ИЛИ выход в ≥{r['coordinator_min_neighbors']} хаба без seed ≥{r['coordinator_min_flow_share']:.0%} выхода. Подтверждающие хабы исключают предварительных кандидатов coordinator.",
        'distributor': f"Получателей ≥{r['distributor_min_recipients']}; правило coordinator не выполнено. При ≥{r['distributor_large_fanout']} получателях это также гарантирует distributor.",
        'consolidator': f"Плательщиков ≥{r['consolidator_min_payers']}; правила coordinator и distributor не выполнены. На обрыве поддержка роли ×{CONFIG['role_scores']['truncated_consolidator_multiplier']:.2f}.",
        'transit': f"Есть вход и выход; {r['transit_min_pass_through']} ≤ выход/вход ≤ {r['transit_max_pass_through']}; не seed и не обрыв. Более ранние правила не выполнены.",
        'terminal': f"Не seed и не обрыв; есть вход; получено ≥{r['terminal_min_in_kzt']:,.0f} ₸; отдано дальше ≤{r['terminal_max_pass_through']:.0%}. Более ранние правила не выполнены. Гипотеза за период.",
        'peripheral': 'Предыдущие правила не выполнены. Недостаточно признаков выраженной роли; это не вывод об отсутствии риска.',
    }
