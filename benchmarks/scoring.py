"""Pure scoring functions: per-run metrics against a task's ground truth.

Consumes a `benchmarks.transcript.RunTrace` plus a task's `ground_truth`
dict (see benchmarks/tasks/*.yaml for the schema). No I/O, no CLI calls —
runner.py wires this to real transcripts; tests exercise it directly.
"""

from typing import Any, Dict, Optional, Sequence

MECHANICAL_PASS_THRESHOLD = 0.75


def normalize_path(path: str, repo_root: Optional[str] = None) -> str:
    """Normalize a path for comparison: forward slashes, repo-relative, no './' prefix.

    Only strips `repo_root` via an exact prefix match. A path that is NOT
    under `repo_root` (e.g. an absolute path with a different prefix, such
    as a container mount point) is returned unchanged — it will then never
    match a repo-relative `relevant_files` entry, and gets silently counted
    as wasted. This matters because runner.py passes absolute `Read` paths
    against repo-relative ground truth.
    """
    p = path.replace("\\", "/")
    if repo_root:
        root = repo_root.replace("\\", "/").rstrip("/") + "/"
        if p.startswith(root):
            p = p[len(root) :]
    if p.startswith("./"):
        p = p[2:]
    return p


def wasted_read_ratio(
    files: Sequence[str],
    relevant_files: Sequence[str],
    acceptable_extra_files: Optional[Sequence[str]] = None,
    repo_root: Optional[str] = None,
) -> Optional[float]:
    """Fraction of `files` that are outside the ground-truth relevant set.

    Returns None (not zero) when `files` is empty — there is no ratio to
    report, and reporting 0.0 would misleadingly imply "perfectly efficient".
    """
    if not files:
        return None

    allowed = {normalize_path(f, repo_root) for f in relevant_files}
    allowed |= {normalize_path(f, repo_root) for f in (acceptable_extra_files or [])}

    normalized = [normalize_path(f, repo_root) for f in files]
    wasted = [f for f in normalized if f not in allowed]
    return len(wasted) / len(normalized)


def fact_score(facts: Sequence[Dict[str, Any]], answer_text: str) -> float:
    """Fraction of fact groups with at least one `any_of` substring match.

    Matching is case-insensitive substring search against the final answer
    text. Returns 1.0 (trivially satisfied) when `facts` is empty.
    """
    if not facts:
        return 1.0

    answer_lower = answer_text.lower()
    matched = 0
    for group in facts:
        candidates = group.get("any_of", [])
        if any(str(c).lower() in answer_lower for c in candidates):
            matched += 1
    return matched / len(facts)


def file_recall(answer_text: str, target_files: Sequence[str]) -> float:
    """Fraction of `target_files` whose path substring appears in the answer.

    Returns 1.0 (trivially satisfied) when `target_files` is empty.
    """
    if not target_files:
        return 1.0

    answer_lower = answer_text.lower()
    matched = sum(1 for f in target_files if f.lower() in answer_lower)
    return matched / len(target_files)


def mechanical_score(answer_text: str, ground_truth: Dict[str, Any]) -> Dict[str, Any]:
    """Score a final answer against a task's ground truth.

    `must_mention_files` (when non-empty) is the file-recall target — it is
    the stricter, hand-picked subset an answer really must name. When empty,
    file recall falls back to the broader `relevant_files` set, so
    architecture-style tasks (no single required file) still get a
    file-recall signal without over-constraining the answer.

    Returns a dict with file_recall, fact_score, and mechanical_correct
    (bool, pass at >= MECHANICAL_PASS_THRESHOLD).
    """
    must_mention = ground_truth.get("must_mention_files") or []
    file_target = must_mention or ground_truth.get("relevant_files") or []
    facts = ground_truth.get("facts") or []

    recall = file_recall(answer_text, file_target)
    facts_pct = fact_score(facts, answer_text)
    combined = 0.5 * recall + 0.5 * facts_pct

    return {
        "file_recall": recall,
        "fact_score": facts_pct,
        "combined": combined,
        "mechanical_correct": combined >= MECHANICAL_PASS_THRESHOLD,
    }


def judge_prompt(task_prompt: str, ground_truth: Dict[str, Any], answer_text: str) -> str:
    """Build a rubric prompt for an optional LLM-judge verification pass.

    Intended for a separate, cheap `claude -p --json-schema` call; this
    function only builds the prompt text (pure, testable).
    """
    relevant = ", ".join(ground_truth.get("relevant_files", [])) or "(none specified)"
    facts = ground_truth.get("facts") or []
    facts_desc = "; ".join(" OR ".join(g.get("any_of", [])) for g in facts) or "(none specified)"

    return (
        "You are grading an AI coding assistant's answer to a codebase question.\n\n"
        f"Question: {task_prompt}\n\n"
        f"Ground-truth relevant files: {relevant}\n"
        f"Expected facts (each is a group of acceptable phrasings): {facts_desc}\n\n"
        f"Candidate answer:\n{answer_text}\n\n"
        "Score the candidate answer's correctness from 0.0 (wrong/irrelevant) to "
        "1.0 (fully correct and names the right files/facts). Respond with your "
        "verdict and score via the provided JSON schema."
    )


def parse_judge_output(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a judge's structured output into {verdict, score}.

    Tolerant of a missing/malformed score — clamps to [0, 1] and defaults
    to 0.0 rather than raising, since a judge-call failure shouldn't crash
    the whole benchmark run.
    """
    score = raw.get("score", 0.0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return {"verdict": raw.get("verdict", ""), "score": score}


def score_run(
    trace_files: Sequence[str],
    answer_text: str,
    ground_truth: Dict[str, Any],
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Combine wasted-read ratio + mechanical correctness for one run."""
    wasted = wasted_read_ratio(
        trace_files,
        ground_truth.get("relevant_files", []),
        ground_truth.get("acceptable_extra_files", []),
        repo_root=repo_root,
    )
    mech = mechanical_score(answer_text, ground_truth)
    return {"wasted_read_ratio": wasted, **mech}
