"""``nexus-mcp-ci link``, ``unlink`` and ``links``: link other repos to this project (ADR-027).

The project is the working folder. Each linked repo keeps its own index in ``<repo>/.nexus``.
These commands are the only code that writes to the link registry and to the store of a
linked repo. The server opens a linked store for reading only.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from nexus_mcp import linked, links
from nexus_mcp.links import LinksError


def _say(message: str) -> None:
    print(message, file=sys.stderr)


def link_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci link",
        description=(
            "Link another repo to the project in the working folder. The repo keeps its own "
            "index in <repo>/.nexus. It is indexed now when it has no index, or one from an "
            "older version. Run this from the root folder that you index."
        ),
        epilog="See the links: `nexus-mcp-ci links`. Remove one: `nexus-mcp-ci unlink <name>`.",
    )
    p.add_argument("path", nargs="?", help="Folder of the repo to link")
    p.add_argument("--name", help="Name of the link (default: the folder name)")
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Index every linked repo again, for files that changed. Takes no path",
    )
    return p


def unlink_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci unlink",
        description="Remove a link. The index of the repo stays in its .nexus folder.",
    )
    p.add_argument("name", help="Name of the link (see `nexus-mcp-ci links`)")
    return p


def links_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="nexus-mcp-ci links",
        description="List the repos linked to the project in the working folder, with their state.",
    )


def _read_meta(path: Path) -> Optional[dict]:
    try:
        data = json.loads((links.store_dir(path) / "index_metadata.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _index_reason(path: Path, pipeline) -> Optional[str]:
    """Why the repo at `path` needs a full index, or None when its index is usable.

    Raises LinksError when a store exists for other roots: a full index would replace it.
    """
    meta = _read_meta(path)
    if meta is None or not isinstance(meta.get("mtimes"), dict):
        return "no usable index metadata"
    if not linked.stored_roots_match(meta, path):
        raise LinksError(
            f"The index in {links.store_dir(path)} was built for other folders, so it would be "
            "replaced. Delete that .nexus folder if you want it replaced, then link again."
        )
    version = meta.get("extractor_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "its index is from an older version"
    # An older number: the graph is built again from the files (`rebuild_graph`), the vectors stay.
    if not pipeline._validate_index():
        return "its index cannot be used (another model, an old format or damage)"
    return None


def build_index(path: Path) -> Tuple[str, int]:
    """Make the store of the repo at `path` current. Returns (repo_id, file count).

    Full index when there is no usable index; otherwise only the changed files. The graph
    is saved after the metadata, because a restore rejects a graph file that is older.
    """
    from nexus_mcp.indexing.pipeline import EXTRACTOR_VERSION, IndexingPipeline, discover_files

    settings = links.settings_for(path)
    # Check first: a pipeline creates the store folder, and an empty repo must get none.
    if not discover_files(path, settings):
        raise LinksError(f"No source files found in {path}. Nothing to link.")
    pipeline = IndexingPipeline(settings)
    if (links.store_dir(path) / "index_metadata.json").exists():
        reason = _index_reason(path, pipeline)
    else:
        reason = "no index yet"
    if reason is not None:
        _say(f"Indexing {path} ({reason}). This can take a while...")
        pipeline.index(path)
    else:
        if (_read_meta(path) or {}).get("extractor_version", 0) < EXTRACTOR_VERSION:
            _say(f"Building the graph of {path} again (routes and calls). No embedding runs.")
            pipeline.rebuild_graph()
        result = pipeline.incremental_index(path)
        if not result.unchanged:
            pipeline._persist_graph()
    meta = _read_meta(path)
    if not meta or not meta.get("repo_id"):
        raise LinksError(f"The index of {path} was not written. Look at the log above.")
    return meta["repo_id"], len(meta.get("mtimes", {}))


def run_link(argv: List[str]) -> int:
    """Run `nexus-mcp-ci link <path> [--name N]` or `link --refresh` for the current project.

    Index the target when it needs one, then save the link in the registry.
    Return 0 on success, 1 when a refresh fails for a repo, 2 on a `LinksError` (printed to stderr).
    """
    args = link_parser().parse_args(argv)
    primary = Path.cwd().resolve()
    try:
        if args.refresh:
            if args.path or args.name:
                raise LinksError("`link --refresh` takes no path and no name.")
            return _refresh(primary)
        if not args.path:
            raise LinksError("Give the folder of the repo to link, or use --refresh.")
        planned = links.plan_link(primary, Path(args.path), args.name)
        repo_id, files = build_index(Path(planned.path))
        link = links.add_link(primary, Path(planned.path), repo_id, planned.name)
    except LinksError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Linked {link.name} -> {link.path}  ({files} files, repo id {link.repo_id[:8]})")
    print("The server attaches it when it starts. `nexus-mcp-ci links` shows the state.")
    return 0


def _refresh(primary: Path) -> int:
    current = links.links_for(primary)
    if not current:
        print("No linked repos. Link one with: nexus-mcp-ci link <path>")
        return 0
    failures = 0
    for link in current:
        path = Path(link.path)
        if not path.is_dir():
            print(f"error: {link.name}: the folder is gone ({link.path})", file=sys.stderr)
            failures += 1
            continue
        try:
            repo_id, files = build_index(path)
            if repo_id != link.repo_id:
                links.add_link(primary, path, repo_id, link.name)
        except LinksError as exc:
            print(f"error: {link.name}: {exc}", file=sys.stderr)
            failures += 1
            continue
        print(f"Refreshed {link.name}  ({files} files)")
    return 1 if failures else 0


def run_unlink(argv: List[str]) -> int:
    """Run `nexus-mcp-ci unlink <name>`. Remove the link; the index of the repo stays on disk.

    Return 0 on success, 2 when the name is not linked (printed to stderr).
    """
    args = unlink_parser().parse_args(argv)
    try:
        removed = links.remove_link(Path.cwd().resolve(), args.name)
    except LinksError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Unlinked {removed.name}. Its index stays in {links.store_dir(Path(removed.path))}.")
    return 0


def _state_text(repo: "linked.LinkedRepo") -> str:
    if repo.skip_reason:
        return f"skipped: {repo.skip_reason}"
    info = linked.staleness(repo, 0)
    if info["stale"]:
        return f"stale ({info['reason']}). Run `nexus-mcp-ci link --refresh`."
    return f"ok ({repo.files} files)"


def run_links(argv: List[str]) -> int:
    """Run `nexus-mcp-ci links`. List the links of the current project with their state.

    Return 0, or 2 when the registry file is not valid.
    """
    links_parser().parse_args(argv)
    from nexus_mcp.config import Settings

    primary = Path.cwd().resolve()
    try:
        links._load(links.registry_path())
    except LinksError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    repos = linked.attach_all(primary, Settings())
    if not repos:
        print("No linked repos. Link one with: nexus-mcp-ci link <path>")
        return 0
    print(f"Linked repos of {primary}:")
    for repo in repos:
        print(f"  {repo.name:<16} {repo.path}\n  {'':<16} {_state_text(repo)}")
    return 0


def run(command: str, argv: List[str]) -> int:
    """Run `link`, `unlink` or `links`."""
    return {"link": run_link, "unlink": run_unlink, "links": run_links}[command](argv)
