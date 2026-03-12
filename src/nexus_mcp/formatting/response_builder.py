"""Response formatting with verbosity levels and token budgets.

Builds structured responses for search and explain tools,
enforcing token budgets per verbosity level.
"""

from typing import Any, Dict, List

from nexus_mcp.formatting.token_budget import TokenBudget


class ResponseBuilder:
    """Build formatted responses with verbosity-aware truncation."""

    def __init__(self, verbosity: str = "detailed"):
        self.budget = TokenBudget(verbosity)
        self.verbosity = verbosity

    def build_search_response(
        self, results: List[Dict[str, Any]], query: str
    ) -> Dict[str, Any]:
        """Format search results according to verbosity level.

        - summary: id, name, score, filepath only
        - detailed: + signature, docstring snippet, line range
        - full: + code text, relationships
        """
        formatted = []
        used_chars = 0

        for r in results:
            if self.verbosity == "summary":
                entry = {
                    "id": r.get("id", ""),
                    "symbol_name": r.get("symbol_name", ""),
                    "score": round(r.get("score", 0.0), 4),
                    "filepath": r.get("filepath", ""),
                }
            elif self.verbosity == "detailed":
                entry = {
                    "id": r.get("id", ""),
                    "symbol_name": r.get("symbol_name", ""),
                    "symbol_type": r.get("symbol_type", ""),
                    "score": round(r.get("score", 0.0), 4),
                    "filepath": r.get("filepath", ""),
                    "line_start": r.get("line_start"),
                    "line_end": r.get("line_end"),
                    "signature": r.get("signature", ""),
                    "docstring": self.budget.truncate(
                        r.get("docstring", ""), reserve=used_chars
                    )[:200],
                }
            else:  # full
                entry = dict(r)
                entry["score"] = round(entry.get("score", 0.0), 4)

            entry_size = sum(len(str(v)) for v in entry.values())
            if used_chars + entry_size > self.budget.budget_chars:
                break
            used_chars += entry_size
            formatted.append(entry)

        return {
            "query": query,
            "total": len(formatted),
            "verbosity": self.verbosity,
            "results": formatted,
        }

    def build_explain_response(
        self,
        symbol_data: Dict[str, Any],
        search_results: List[Dict[str, Any]],
        analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a structured explanation combining graph, vector, and analysis data.

        Args:
            symbol_data: Symbol definition and relationships from graph.
            search_results: Related code from vector search.
            analysis: Code analysis metrics.
        """
        explanation: Dict[str, Any] = {
            "symbol": symbol_data,
        }

        # Add related code (truncated by verbosity)
        if self.verbosity == "summary":
            explanation["related_count"] = len(search_results)
        elif self.verbosity == "detailed":
            explanation["related_code"] = [
                {
                    "symbol_name": r.get("symbol_name", ""),
                    "filepath": r.get("filepath", ""),
                    "score": round(r.get("score", 0.0), 4),
                }
                for r in search_results[:5]
            ]
        else:  # full
            explanation["related_code"] = search_results[:10]

        # Add analysis
        if analysis:
            if self.verbosity == "summary":
                explanation["quality_score"] = analysis.get("quality", {}).get(
                    "overall_score", 0
                )
            else:
                explanation["analysis"] = analysis

        explanation["verbosity"] = self.verbosity
        return explanation
