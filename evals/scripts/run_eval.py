#!/usr/bin/env python3
"""run_eval.py — 评测入口脚本.

用法:
    python evals/scripts/run_eval.py --base-url http://127.0.0.1:8000
    python evals/scripts/run_eval.py --categories normal,tool_failure
    python evals/scripts/run_eval.py --case-ids food-001,route-001
    python evals/scripts/run_eval.py --num-trials 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.runners.harness import EvalHarness, HarnessConfig
from evals.reporters.reporters import ConsoleReporter, HtmlReporter, JsonReporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart-Eats-AI Agent Evaluation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--timeout", type=float, default=60.0, help="Single request timeout (seconds)")
    parser.add_argument("--num-trials", type=int, default=3, help="Number of trials per case")
    parser.add_argument("--output-dir", default="./eval_results", help="Output directory")
    parser.add_argument("--dataset-dir", default="./evals/datasets", help="Dataset directory")
    parser.add_argument("--runner", choices=["live", "fixture"], default="live", help="Evaluation runner")
    parser.add_argument("--suite", choices=["quick", "full", "live-smoke"], default="full", help="Evaluation suite")
    parser.add_argument("--fixture-path", default="./evals/datasets/fixture_traces.json", help="Fixture trace file")
    parser.add_argument("--case-ids", default=None, help="Comma-separated case IDs to filter")
    parser.add_argument("--categories", default=None, help="Comma-separated categories to filter")
    parser.add_argument("--scenes", default=None, help="Comma-separated scenes to filter")
    parser.add_argument("--no-html", action="store_true", help="Skip HTML report generation")
    parser.add_argument("--include-llm-judge", action="store_true", help="Run optional DeepEval LLM judge metrics")
    parser.add_argument("--persist-db", dest="persist_db", action="store_true", default=True, help="Persist report to eval database when configured")
    parser.add_argument("--no-persist-db", dest="persist_db", action="store_false", help="Skip eval database persistence")
    parser.add_argument("--eval-database-url", default=None, help="Evaluation PostgreSQL database URL")
    parser.add_argument("--require-db-persist", action="store_true", help="Fail evaluation when DB persistence fails")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = HarnessConfig(
        base_url=args.base_url,
        timeout=args.timeout,
        num_trials=args.num_trials,
        output_dir=args.output_dir,
        dataset_dir=args.dataset_dir,
        runner=args.runner,
        suite=args.suite,
        fixture_path=args.fixture_path,
        include_llm_judge=args.include_llm_judge,
    )

    harness = EvalHarness(config)

    # 解析筛选参数
    case_ids = args.case_ids.split(",") if args.case_ids else None
    categories = args.categories.split(",") if args.categories else None
    scenes = args.scenes.split(",") if args.scenes else None

    # 运行评测
    report = asyncio.run(
        harness.run(case_ids=case_ids, categories=categories, scenes=scenes)
    )

    # 输出报告
    console = ConsoleReporter()
    console.report(report)

    json_reporter = JsonReporter(
        output_dir=args.output_dir,
        metadata={
            "suite": args.suite,
            "runner": args.runner,
            "base_url": args.base_url if args.runner == "live" else None,
            "include_llm_judge": args.include_llm_judge,
        },
    )
    json_path = json_reporter.report(report)
    print(f"\n📄 JSON report: {json_path}")

    if args.persist_db:
        from evals.persistence.postgres import resolve_eval_database_url

        database_url = resolve_eval_database_url(args.eval_database_url)
        if database_url:
            try:
                from evals.persistence.postgres import EvalPersistenceStore, normalize_report_name

                report_data = json.loads(Path(json_path).read_text(encoding="utf-8"))
                report_name = normalize_report_name(json_path, report_data)
                store = EvalPersistenceStore(database_url)
                try:
                    asyncio.run(store.upsert_report(report_name, report_data))
                finally:
                    asyncio.run(store.close())
                print(f"🗄️  Eval DB persisted: {report_name}")
            except Exception as exc:
                message = f"Eval DB persistence failed: {exc}"
                if args.require_db_persist:
                    raise RuntimeError(message) from exc
                logger.warning(message)
        else:
            logger.info("Eval DB persistence skipped: EVAL_DATABASE_URL is not set")

    if not args.no_html:
        html_reporter = HtmlReporter(output_dir=args.output_dir)
        html_path = html_reporter.report(report)
        print(f"📊 HTML report: {html_path}")

    # 检查阈值
    from evals.scripts.check_thresholds import check_thresholds
    threshold_config = config.thresholds
    passed, failures = check_thresholds(report, threshold_config)

    if not passed:
        print("\n❌ 阈值检查未通过:")
        for metric, actual, threshold in failures:
            print(f"  {metric}: {actual:.1%} < {threshold:.1%}")
        sys.exit(1)
    else:
        print("\n✅ 所有阈值检查通过!")


if __name__ == "__main__":
    main()
