"""Cross-language code analyzer.

Ported from code-graph-mcp's universal_ast.py. Provides complexity analysis,
code smell detection, dependency analysis, and quality metrics.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Set

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

logger = logging.getLogger(__name__)


class CodeAnalyzer:
    """High-level code analysis using the graph engine."""

    def __init__(self, graph: RustworkxCodeGraph):
        self.graph = graph

    def detect_code_smells(self) -> Dict[str, List[Dict[str, Any]]]:
        """Detect code smells across the codebase."""
        smells: Dict[str, List[Dict[str, Any]]] = {
            "long_functions": [],
            "complex_functions": [],
            "large_classes": [],
            "dead_code": [],
        }

        functions = self.graph.get_nodes_by_type(NodeType.FUNCTION)
        for func in functions:
            if func.line_count > 50:
                smells["long_functions"].append({
                    "name": func.name,
                    "location": f"{func.location.file_path}:{func.location.start_line}",
                    "line_count": func.line_count,
                    "severity": "high" if func.line_count > 100 else "medium",
                })
            if func.complexity > 15:
                smells["complex_functions"].append({
                    "name": func.name,
                    "location": f"{func.location.file_path}:{func.location.start_line}",
                    "complexity": func.complexity,
                    "severity": "high" if func.complexity > 20 else "medium",
                })

        classes = self.graph.get_nodes_by_type(NodeType.CLASS)
        for cls in classes:
            rels = self.graph.get_relationships_from(cls.id)
            method_count = sum(
                1 for r in rels if r.relationship_type == RelationshipType.CONTAINS
            )
            if method_count > 20:
                smells["large_classes"].append({
                    "name": cls.name,
                    "location": f"{cls.location.file_path}:{cls.location.start_line}",
                    "method_count": method_count,
                    "severity": "high" if method_count > 30 else "medium",
                })

        smells["dead_code"] = self._find_dead_code()
        return smells

    def analyze_complexity(self, threshold: int = 10) -> Dict[str, Any]:
        """Analyze complexity distribution."""
        functions = self.graph.get_nodes_by_type(NodeType.FUNCTION)
        if not functions:
            return {
                "total_functions": 0,
                "average_complexity": 0.0,
                "max_complexity": 0,
                "high_complexity_functions": [],
                "complexity_distribution": {},
            }

        complexities = [f.complexity for f in functions if f.complexity > 0]
        if not complexities:
            return {
                "total_functions": len(functions),
                "average_complexity": 0.0,
                "max_complexity": 0,
                "high_complexity_functions": [],
                "complexity_distribution": {},
            }

        distribution: Dict[str, int] = defaultdict(int)
        for c in complexities:
            if c <= 5:
                distribution["simple"] += 1
            elif c <= 10:
                distribution["moderate"] += 1
            elif c <= 20:
                distribution["complex"] += 1
            else:
                distribution["very_complex"] += 1

        high = [
            {
                "name": f.name,
                "complexity": f.complexity,
                "location": f"{f.location.file_path}:{f.location.start_line}",
            }
            for f in functions
            if f.complexity >= threshold
        ]
        high.sort(key=lambda x: x["complexity"], reverse=True)

        return {
            "total_functions": len(functions),
            "average_complexity": sum(complexities) / len(complexities),
            "max_complexity": max(complexities),
            "high_complexity_functions": high,
            "complexity_distribution": dict(distribution),
        }

    def analyze_dependencies(self) -> Dict[str, Any]:
        """Analyze module dependencies and coupling."""
        import_rels = self.graph.get_relationships_by_type(RelationshipType.IMPORTS)

        deps: Dict[str, Set[str]] = defaultdict(set)
        for rel in import_rels:
            src = self.graph.get_node(rel.source_id)
            if src and src.node_type == NodeType.MODULE:
                target = rel.target_id.replace("module:", "")
                deps[src.name].add(target)

        total = sum(len(d) for d in deps.values())
        highly_coupled = [
            {"module": m, "dependency_count": len(d), "dependencies": sorted(d)}
            for m, d in deps.items()
            if len(d) > 5
        ]

        circular = self._detect_circular_deps(deps)

        return {
            "total_modules": len(deps),
            "total_dependencies": total,
            "highly_coupled_modules": highly_coupled,
            "circular_dependencies": circular,
        }

    def calculate_quality_metrics(self) -> Dict[str, Any]:
        """Calculate overall code quality metrics."""
        functions = self.graph.get_nodes_by_type(NodeType.FUNCTION)
        if not functions:
            return {
                "maintainability_index": 0,
                "documentation_ratio": 0,
                "quality_score": 0,
            }

        complexities = [f.complexity for f in functions if f.complexity > 0]
        avg_complexity = sum(complexities) / len(complexities) if complexities else 1

        total_lines = sum(
            n.line_count for n in self.graph.nodes.values() if n.line_count > 0
        )
        maintainability = max(0, 100 - (avg_complexity * 5) - (total_lines / 1000))

        documented = len([f for f in functions if f.docstring])
        doc_ratio = (documented / len(functions)) * 100 if functions else 0

        quality_score = (maintainability * 0.6 + doc_ratio * 0.4)
        quality_score = max(0, min(100, quality_score))

        return {
            "maintainability_index": round(maintainability, 2),
            "documentation_ratio": round(doc_ratio, 2),
            "quality_score": round(quality_score, 2),
        }

    def _find_dead_code(self) -> List[Dict[str, Any]]:
        """Find functions that are never called."""
        functions = {n.id: n for n in self.graph.get_nodes_by_type(NodeType.FUNCTION)}
        called: Set[str] = set()

        for rel in self.graph.get_relationships_by_type(RelationshipType.CALLS):
            called.add(rel.target_id)

        dead = []
        entry_patterns = {
            "main", "__main__", "init", "__init__", "setup", "run",
            "start", "handler", "callback", "test_",
        }
        for fid, func in functions.items():
            if fid not in called:
                if not any(p in func.name.lower() for p in entry_patterns):
                    dead.append({
                        "name": func.name,
                        "location": f"{func.location.file_path}:{func.location.start_line}",
                        "reason": "Never called",
                    })
        return dead

    def _detect_circular_deps(self, deps: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        """Detect circular dependencies using DFS."""
        circular: List[Dict[str, Any]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str, path: List[str]) -> None:
            if node in rec_stack:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                circular.append({"cycle": cycle, "length": len(cycle) - 1})
                return
            if node in visited:
                return
            visited.add(node)
            rec_stack.add(node)
            for neighbor in deps.get(node, set()):
                dfs(neighbor, path + [node])
            rec_stack.remove(node)

        for module in deps:
            if module not in visited:
                dfs(module, [])

        return circular
