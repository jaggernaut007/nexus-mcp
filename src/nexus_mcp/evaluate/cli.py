"""Command line for ``nexus-mcp-ci eval``, ``data`` and ``setup``."""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from nexus_mcp.evaluate import data_cmd, doctor, paths, report, source, specs, store
from nexus_mcp.privacy import apply_privacy_defaults

SUBCOMMANDS = ("eval", "data", "setup", "license")


def _eval_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci eval",
        description=(
            "Measure Nexus-MCP against Grep and Read on questions made from your own code. "
            "Free, offline, no LLM. Nothing leaves your machine."
        ),
        epilog="Saved runs: `eval list`, `eval show <id>`. Your data: `nexus-mcp-ci data where`.",
    )
    p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="A folder (default: current folder) or a public https:// repo URL, "
        "or `list` / `show <id>` / `share <id>` / `doctor`",
    )
    p.add_argument("run_id", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--example",
        action="store_true",
        help="Use the Nexus-MCP source code as the example project (offline)",
    )
    p.add_argument("--questions", type=int, default=30, help="Number of questions (default 30)")
    p.add_argument("--seed", type=int, default=7, help="Seed for the question sample (default 7)")
    p.add_argument(
        "--keep-index",
        action="store_true",
        help="Keep the index in <project>/.nexus so adopting Nexus-MCP needs no re-index",
    )
    p.add_argument(
        "--out", type=Path, default=None, help="Also copy report.md and report.json here"
    )
    p.add_argument("--json", action="store_true", help="Print the result as JSON")
    agent = p.add_argument_group(
        "agent tier (paid, opt-in)",
        "A real A/B run of Claude Code or Codex with and without Nexus-MCP. It sends prompts "
        "and the code the agent reads to the provider, through your own CLI and login.",
    )
    agent.add_argument(
        "--agent",
        metavar="SPECS",
        help="runner[:model] list, for example claude, claude:haiku,claude:sonnet or "
        "codex:gpt-5-codex (Codex is experimental)",
    )
    agent.add_argument(
        "--preset",
        choices=sorted(specs.PRESETS),
        help="quick: claude:sonnet, 4 tasks. matrix: every --agent model. "
        "efficient: also the mcp condition, then the cheapest setup at the same accuracy",
    )
    agent.add_argument(
        "--conditions", help="Comma list of baseline, mcp, nexus (default: baseline,nexus)"
    )
    agent.add_argument("--tasks", type=int, default=None, help="Tasks per condition (default 4)")
    agent.add_argument(
        "--max-usd", type=float, default=5.0, help="Stop starting runs at this spend (default 5)"
    )
    agent.add_argument("--concurrency", type=int, default=3, help="Parallel runs (default 3)")
    agent.add_argument(
        "--yes", action="store_true", help="Skip the question before a paid run or a share"
    )
    return p


def _share(args) -> int:
    """Show the allow-listed payload of a saved run, then post it only after a yes."""
    from nexus_mcp.evaluate import share

    if not args.run_id:
        print("Usage: nexus-mcp-ci eval share <run-id>   (see: nexus-mcp-ci eval list)")
        return 2
    try:
        payload = share.build_payload(store.load(args.run_id))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print("This is the whole payload. It has no paths, no code and no question text.\n")
    print(share.issue_body(payload))
    if args.yes:
        answer = "y"
    elif sys.stdin.isatty():
        answer = input(f"Post it as a public issue on github.com/{share.REPO}? [y/N] ")
    else:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Not posted. Nothing left this machine.")
        return 0
    if share.gh_ready():
        url = share.post(payload)
        if url is not None:
            print(f"Posted: {url}")
            return 0
        print("gh could not create the issue.", file=sys.stderr)
    print(f"Open {share.FORM_URL} and paste the JSON above into the form.")
    return 0


def _agent_choices(args):
    """Specs, conditions and task count from --agent, --preset and --conditions."""
    preset = specs.PRESETS.get(args.preset or "", {})
    spec_text = args.agent or ",".join(preset.get("specs") or []) or "claude"
    chosen = specs.parse_specs(spec_text)
    if args.conditions:
        conditions = specs.parse_conditions(args.conditions)
    else:
        conditions = list(preset.get("conditions") or ["baseline", "nexus"])
    tasks = args.tasks or preset.get("tasks") or 4
    return chosen, conditions, tasks


def _confirm(args, say):
    def ask(plan) -> bool:
        say(
            f"Agent tier: {plan['runs']} runs, rough estimate ${plan['estimate_usd']:.2f}, "
            f"hard stop at ${plan['max_usd']:.2f}. Prompts and the code the agent reads go "
            "to the provider through your CLI."
        )
        if args.yes:
            return True
        if not sys.stdin.isatty():
            say("Not a terminal: add --yes to run the agent tier.")
            return False
        return input("Start? [y/N] ").strip().lower() in ("y", "yes")

    return ask


def _data_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci data",
        description="Show, export and delete the data that Nexus-MCP stores on this machine.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    where = sub.add_parser("where", help="List every place data is kept, with sizes")
    where.add_argument("path", nargs="?", default=".")
    clear = sub.add_parser("clear", help="Delete stored data (dry run unless --yes)")
    clear.add_argument("path", nargs="?", default=".")
    for target in data_cmd.TARGETS:
        clear.add_argument(f"--{target}", action="store_true", help=f"Delete the {target}")
    clear.add_argument("--all", action="store_true", help="All of the above")
    clear.add_argument(
        "--yes", action="store_true", help="Really delete (the default is a dry run)"
    )
    export = sub.add_parser("export-memories", help="Write memories to a JSON file")
    export.add_argument("path", nargs="?", default=".")
    export.add_argument("--out", type=Path, default=Path("nexus-memories.json"))
    return p


def run_eval(argv: List[str]) -> int:
    args = _eval_parser().parse_args(argv)
    if args.source == "list":
        rows = store.history()
        for row in rows:
            print(f"{row['id']}  {row.get('verdict') or '-':<11} {row.get('source') or '-'}")
        if not rows:
            print("No saved runs yet. Run: nexus-mcp-ci eval")
        return 0
    if args.source == "show":
        if not args.run_id:
            print("Usage: nexus-mcp-ci eval show <run-id>", file=sys.stderr)
            return 2
        try:
            record = store.load(args.run_id)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        md = paths.eval_home() / args.run_id / "report.md"
        print(json.dumps(record, indent=2) if args.json else md.read_text())
        return 0

    if args.source == "share":
        return _share(args)
    if args.source == "doctor":
        print(doctor.render(doctor.checks()))
        return 0

    apply_privacy_defaults()
    say = (lambda msg: None) if args.json else (lambda msg: print(msg, file=sys.stderr))
    agent_mode = bool(args.agent or args.preset)
    if agent_mode:
        try:
            chosen, conditions, tasks = _agent_choices(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        if args.source and "://" in args.source and not args.json:
            say(f"Network: git clone of {args.source} (shallow).")
        src = source.resolve(args.source, args.example)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if agent_mode:
            from nexus_mcp.evaluate.agent import run_agent

            result = run_agent(
                src.root,
                chosen,
                conditions,
                tasks=tasks,
                seed=args.seed,
                max_usd=args.max_usd,
                concurrency=args.concurrency,
                confirm=_confirm(args, say),
                progress=say,
            )
        else:
            from nexus_mcp.evaluate.quick import run_quick

            result = run_quick(
                src.root,
                count=args.questions,
                seed=args.seed,
                keep_index=args.keep_index,
                progress=say,
            )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if src.cleanup:
            shutil.rmtree(src.cleanup, ignore_errors=True)

    run_id = store.new_run_id()
    public = src.kind in ("url", "example")
    record = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "kind": src.kind,
            "label": src.label if public else src.root.name,
            "label_public": src.label if public else None,
            "sha": src.sha,
        },
        "kept_index": args.keep_index,
        "env": store.environment(),
        **result,
    }
    run_dir = paths.eval_home() / run_id
    render = report.render_agent if agent_mode else report.render
    markdown = render(record, str(run_dir))
    store.save(run_id, record, markdown)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.md").write_text(markdown)
        (args.out / "report.json").write_text(json.dumps({"id": run_id, **record}, indent=2))
    print(json.dumps({"id": run_id, **record}, indent=2) if args.json else markdown)
    if not args.json:
        print(f"Saved: {run_dir}", file=sys.stderr)
    return 1 if record.get("error") else 0


def run_data(argv: List[str]) -> int:
    args = _data_parser().parse_args(argv)
    project = Path(args.path).resolve()
    if args.action == "where":
        print(data_cmd.where(project))
        return 0
    if args.action == "export-memories":
        try:
            count = data_cmd.export_memories(project, args.out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {count} memories to {args.out}")
        return 0
    targets = (
        list(data_cmd.TARGETS) if args.all else [t for t in data_cmd.TARGETS if getattr(args, t)]
    )
    if not targets:
        print(
            "Choose what to delete: " + ", ".join(f"--{t}" for t in data_cmd.TARGETS) + ", --all",
            file=sys.stderr,
        )
        return 2
    try:
        outcome = data_cmd.clear(project, targets, args.yes)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not outcome["plan"]:
        print("Nothing to delete.")
        return 0
    verb = "Would delete" if outcome["dry_run"] else "Deleted"
    for item in outcome["plan"]:
        print(f"{verb}: {item['path']}  ({paths.human_size(item['bytes'])}, {item['files']} files)")
    if outcome["dry_run"]:
        print("Dry run. Nothing was deleted. Add --yes to delete. Stop your MCP client first.")
    return 0


def dispatch(argv: List[str]) -> Optional[int]:
    """Run a subcommand and return its exit code, or None when ``argv`` is not one."""
    if not argv or argv[0] not in SUBCOMMANDS:
        return None
    if argv[0] == "license":
        from nexus_mcp.licensing import run_license

        return run_license(argv[1:])
    if argv[0] == "setup":
        from nexus_mcp.setup_clients import run_setup

        return run_setup(argv[1:])
    return run_eval(argv[1:]) if argv[0] == "eval" else run_data(argv[1:])
