"""Render ver5 result graphs from results/v5_state.json."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS = SCRIPT_DIR / "results"
state = json.loads((RESULTS / "v5_state.json").read_text())
iters = state["iterations"]
xs = [r["iter"] for r in iters]
co = lambda r, k: r["valB"]["co"].get(str(k), r["valB"]["co"].get(k))

# A. scores vs iteration
fig, ax = plt.subplots(figsize=(9, 5))
ax.axhline(54.4, color="#BBB", ls="--", lw=1)
ax.annotate("ver4 co@20 baseline 54.4", (xs[0], 55), fontsize=8, color="#888")
series = [
    ("ValB co@20", [co(r, 20) for r in iters], "#F44336", "o"),
    ("ValB co@10", [co(r, 10) for r in iters], "#FF9800", "s"),
    ("ValB co@5", [co(r, 5) for r in iters], "#9E9E9E", "^"),
    ("All@20", [r["all"]["hit@20"] for r in iters], "#2196F3", "D"),
    ("ValA@20", [r["valA"]["hit@20"] for r in iters], "#4CAF50", "v"),
]
for label, ys, c, m in series:
    ax.plot(xs, ys, marker=m, color=c, label=label)
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=7)
ax.set_xlabel("Iteration"); ax.set_ylabel("recall (%)"); ax.set_ylim(0, 105)
ax.set_xticks(xs)
ax.set_title("A. StructRAG ver5 — scores per iteration (two-track evolution)")
ax.legend(fontsize=8, loc="lower right"); ax.grid(axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig(RESULTS / "graph_A_scores.png", dpi=150); plt.close(fig)

# B. evolution visibility: SG layers + plan cache hit rate
fig, ax1 = plt.subplots(figsize=(9, 4.5))
ax1.plot(xs, [r["sg"]["l2_co_retrieval"] for r in iters], marker="o", color="#7E57C2", label="L2 co-retrieval edges")
ax1.plot(xs, [r["sg"]["l3_llm_curated"] * 50 for r in iters], marker="s", color="#26A69A", label="L3 curated edges (×50)")
ax1.plot(xs, [r["sg"]["profiles"] * 50 for r in iters], marker="^", color="#8D6E63", label="doc profiles (×50)")
ax1.set_xlabel("Iteration"); ax1.set_ylabel("SG learned edges"); ax1.set_xticks(xs)
ax2 = ax1.twinx()
ax2.plot(xs, [r["plan_cache"].get("hit_rate", 0) * 100 for r in iters],
         marker="D", color="#EF6C00", ls="--", label="plan-cache hit %")
ax2.set_ylabel("plan-cache hit rate (%)"); ax2.set_ylim(0, 100)
lines1, l1 = ax1.get_legend_handles_labels(); lines2, l2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, l1 + l2, fontsize=8, loc="upper left")
ax1.set_title("B. Evolution visibility — structure graph growth & plan-cache warm-up")
ax1.grid(axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig(RESULTS / "graph_B_evolution.png", dpi=150); plt.close(fig)

print("graphs written:", RESULTS / "graph_A_scores.png", RESULTS / "graph_B_evolution.png")
