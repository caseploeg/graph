"""
Debug statistics for question generation.

Collects and formats diagnostic information to understand
why repos produce few or zero questions.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CandidateRejection:
    """Record of a rejected candidate node."""
    node_id: int
    node_name: str
    node_type: str
    reason: str
    connection_count: int


@dataclass
class QuestionDebugStats:
    """Collects debug statistics for question generation."""

    # Graph structure stats
    node_counts: dict[str, int] = field(default_factory=dict)
    relationship_counts: dict[str, int] = field(default_factory=dict)

    # Candidate selection stats
    total_functions: int = 0
    total_methods: int = 0
    total_classes: int = 0
    total_modules: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0

    # Rejection tracking
    rejections: list[CandidateRejection] = field(default_factory=list)
    rejection_reasons: Counter = field(default_factory=Counter)

    def add_rejection(
        self,
        node_id: int,
        node_name: str,
        node_type: str,
        reason: str,
        connection_count: int,
    ) -> None:
        """Record a candidate rejection."""
        self.rejections.append(CandidateRejection(
            node_id=node_id,
            node_name=node_name,
            node_type=node_type,
            reason=reason,
            connection_count=connection_count,
        ))
        self.rejection_reasons[reason] += 1
        self.candidates_rejected += 1

    def format_summary(self, verbose: bool = False, max_rejections: int = 10) -> str:
        """Format stats as human-readable summary."""
        lines = []

        # Node counts
        lines.append("=== NODE COUNTS ===")
        for label, count in sorted(self.node_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {label:20s}: {count:,}")

        # Relationship counts
        lines.append("\n=== RELATIONSHIP COUNTS ===")
        for rel_type, count in sorted(self.relationship_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {rel_type:20s}: {count:,}")

        # Candidate summary
        lines.append("\n=== CANDIDATE SELECTION ===")
        lines.append(f"  Functions examined:   {self.total_functions:,}")
        lines.append(f"  Methods examined:     {self.total_methods:,}")
        lines.append(f"  Classes examined:     {self.total_classes:,}")
        lines.append(f"  Modules examined:     {self.total_modules:,}")
        lines.append(f"  Candidates accepted:  {self.candidates_accepted:,}")
        lines.append(f"  Candidates rejected:  {self.candidates_rejected:,}")

        # Rejection reasons
        if self.rejection_reasons:
            lines.append("\n=== REJECTION REASONS ===")
            for reason, count in self.rejection_reasons.most_common():
                lines.append(f"  {count:5,}x {reason}")

        # Sample rejections (verbose mode)
        if verbose and self.rejections:
            lines.append(f"\n=== SAMPLE REJECTIONS (first {max_rejections}) ===")
            for rej in self.rejections[:max_rejections]:
                lines.append(
                    f"  [{rej.node_type:8s}] {rej.node_name[:40]:40s} "
                    f"connections={rej.connection_count}"
                )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "node_counts": self.node_counts,
            "relationship_counts": self.relationship_counts,
            "total_functions": self.total_functions,
            "total_methods": self.total_methods,
            "total_classes": self.total_classes,
            "total_modules": self.total_modules,
            "candidates_accepted": self.candidates_accepted,
            "candidates_rejected": self.candidates_rejected,
            "rejection_reasons": dict(self.rejection_reasons),
        }
