from scripts.agent_metrics_summary import parse_metrics


def test_parse_metrics_counts_metric_lines():
    text = '\n'.join([
        'INFO metric {"metric":"intent_decision","value":1}',
        'INFO metric {"metric":"intent_decision","value":1}',
        'INFO metric {"metric":"fallback_final","value":1}',
    ])

    stats = parse_metrics(text)

    assert stats["intent_decision"] == 2
    assert stats["fallback_final"] == 1
