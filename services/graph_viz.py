"""Circular-trading graph visualization builder (Requirement 29).

Produces a GraphVisualization spec (nodes, edges, highlighted cycle, legend,
summary) from detected fraud cycles. A PNG (>=300 DPI) is rendered when
matplotlib is available; otherwise the spec is returned for the renderer to
embed as a table/structured figure in the CAM.
"""
from __future__ import annotations

from typing import Optional

from config.storage import storage
from models.cam import GraphVisualization
from models.scoring import FraudReport
from utils.logging import get_logger

logger = get_logger(__name__)

_LEGEND = {
    "self_node": "Borrower entity (blue)",
    "counterparty_node": "Counterparty entity (grey)",
    "edge": "Directed payment with amount and date",
    "cycle_edge": "Highlighted circular path (red)",
}


def build_graph_visualization(fraud: Optional[FraudReport]) -> Optional[GraphVisualization]:
    """Build a graph visualization for the most material circular cycle."""
    if not fraud:
        return None
    cycles = [c for f in fraud.findings if f.kind == "circular_trading" for c in f.cycles]
    if not cycles:
        return None

    cycle = max(cycles, key=lambda c: c.total_value)
    node_ids = list(dict.fromkeys(cycle.entity_chain))
    nodes = [
        {"id": n, "gstin": n, "label": n, "type": "self" if i == 0 else "counterparty"}
        for i, n in enumerate(node_ids)
    ]
    edges = cycle.edges
    summary = {
        "total_value": cycle.total_value,
        "span_days": cycle.span_days,
        "hops": max(0, len(cycle.entity_chain) - 1),
        "entities": len(node_ids),
    }
    viz = GraphVisualization(
        nodes=nodes,
        edges=edges,
        cycle_path=cycle.entity_chain,
        cycle_summary=summary,
        legend=_LEGEND,
    )
    viz.image_key = _try_render(viz)
    return viz



def _try_render(viz: GraphVisualization) -> Optional[str]:
    """Render the cycle to a >=300 DPI PNG if matplotlib is available."""
    try:
        import math

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        logger.info("matplotlib unavailable; graph image not rendered (spec retained)")
        return None

    try:
        nodes = viz.cycle_path[:-1] if (len(viz.cycle_path) > 1 and viz.cycle_path[0] == viz.cycle_path[-1]) else viz.cycle_path
        n = max(1, len(nodes))
        positions = {
            node: (math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
            for i, node in enumerate(nodes)
        }
        fig, ax = plt.subplots(figsize=(6, 6))
        for e in viz.edges:
            src, dst = e["from"], e["to"]
            if src in positions and dst in positions:
                x1, y1 = positions[src]
                x2, y2 = positions[dst]
                ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                            arrowprops=dict(arrowstyle="->", color="red", lw=2))
                ax.text((x1 + x2) / 2, (y1 + y2) / 2, f"{e['amount']:,.0f}", fontsize=7, color="darkred")
        for node, (x, y) in positions.items():
            ax.scatter([x], [y], s=900, c="#1f77b4", zorder=3)
            ax.text(x, y, node, ha="center", va="center", color="white", fontsize=8, zorder=4)
        ax.set_title("Circular Trading Cycle")
        ax.axis("off")
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        plt.close(fig)
        key = storage.put(buf.getvalue(), key=f"graphs/cycle_{id(viz)}.png", content_type="image/png")
        logger.info("Rendered circular-trading graph to %s", key)
        return key
    except Exception as exc:  # pragma: no cover - rendering is best-effort
        logger.warning("Graph render failed: %s", exc)
        return None
