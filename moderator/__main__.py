import argparse
import json
import sys

from .moderator import Moderator, load_env


def main():
    p = argparse.ArgumentParser(description="ARGUS moderator (provisional): dispatch, monitor, combine")
    p.add_argument("--spec", help="Path to a run spec JSON (task + subtasks)")
    p.add_argument("--task", help="Quick mode: single plain-language task")
    p.add_argument("--url", help="Quick mode: start URL for the single subtask")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    load_env()
    if args.spec:
        with open(args.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
    elif args.task:
        spec = {
            "schema_version": "0.1-provisional",
            "task": args.task,
            "subtasks": [{"subtask_id": "subtask-1", "subtask": args.task, "start_url": args.url}],
        }
    else:
        p.error("provide --spec or --task")

    result = Moderator(spec, out_dir=args.out_dir).run()
    print("\n=== FINAL ANSWER ===")
    print(result["final_answer"])
    sys.exit(0 if result["status"] == "succeeded" else 1)


if __name__ == "__main__":
    main()
