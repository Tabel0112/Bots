import argparse
import json
import sys

from . import load_env


def main():
    p = argparse.ArgumentParser(description="VLM browser subagent (Steel + local UI-TARS or Claude)")
    p.add_argument("subtask", help="Natural-language subtask from the moderator agent")
    p.add_argument("--url", default=None, help="Starting URL")
    p.add_argument("--backend", choices=["uitars", "claude"], default="uitars")
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint for uitars backend")
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--log-dir", default=None)
    p.add_argument("--request-id", default=None)
    p.add_argument("--subtask-id", default=None)
    args = p.parse_args()

    load_env()
    if args.backend == "uitars":
        from .uitars_agent import UITarsSubagent
        agent = UITarsSubagent(base_url=args.base_url, model=args.model or "ui-tars",
                               max_steps=args.max_steps, log_dir=args.log_dir)
    else:
        from .vlm_agent import BrowserSubagent
        agent = BrowserSubagent(model=args.model or "claude-opus-5",
                                max_steps=args.max_steps, log_dir=args.log_dir)
    result = agent.run(args.subtask, start_url=args.url)

    from .worker_report import save_worker_report
    report_path, report = save_worker_report(result, args.subtask,
                                             request_id=args.request_id,
                                             subtask_id=args.subtask_id)
    result["report_file"] = report_path
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
