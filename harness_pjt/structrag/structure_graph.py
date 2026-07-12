"""
Structure Graph (SG) — document-level structural relations, stored SEPARATELY from the KG.

Three evidence layers per edge (see DESIGN_HISTORY.md D2, research_notes.md §1):
  L1 explicit_ref  — deterministic scan of chunk texts for filename / doc-ID mentions.
                     Immutable once built. No LLM. Generic patterns (no corpus-specific rules).
  L2 co_retrieval  — reinforced/decayed from co-retrieval statistics of HIGH-QUALITY queries
                     only (quality gate keeps bad retrievals from polluting structure).
  L3 llm_curated   — typed relations proposed by the background evolver LLM from evidence
                     bundles. Must cite chunk evidence. Can be decayed/removed.

The SG never touches the KG or any VDB (ver4 lesson: structural stats in the
relation VDB degraded base retrieval). All mutations are appended to
structrag_meta/evolution_log.jsonl with provenance and reason.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict

# Effective-weight multipliers per layer: explicit refs are ground truth from the
# documents themselves; learned layers rank below until heavily reinforced.
LAYER_FACTOR = {"explicit_ref": 1.0, "llm_curated": 0.8, "co_retrieval": 0.6}

# Generic doc-ID token: uppercase segments joined by hyphens, at least one digit
# (e.g. AUR-940, SPEC-RTL-AIM-0001, VT-AIM-DEC-0001). Built to be corpus-agnostic.
_ID_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{2,8}){1,3}\b")

# Bump when L1 extraction logic changes — a stored SG with an older version gets
# its explicit_ref layer rebuilt on the next build_l1() call (learned layers kept).
L1_BUILDER_VERSION = 2


def _pair_key(a: str, b: str) -> str:
    return f"{a}||{b}" if a <= b else f"{b}||{a}"


class StructureGraph:
    """
    Nodes:  doc basename → {"dir": top-level dir, "ext": suffix, "tokens": [...],
                            "profile": str|None (LLM doc profile, evolves)}
    Edges:  "a||b" → {"layers": {layer_name: {"w": float, ...layer fields}},
                      "hits": int, "last_hit": ts}
    """

    def __init__(self, working_dir: str):
        self._meta_dir = os.path.join(working_dir, "structrag_meta")
        os.makedirs(self._meta_dir, exist_ok=True)
        self._path = os.path.join(self._meta_dir, "structure_graph.json")
        self._log_path = os.path.join(self._meta_dir, "evolution_log.jsonl")
        self._data = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"nodes": {}, "edges": {}, "built_l1": False}

    def save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)

    def log(self, action: str, **fields):
        rec = {"ts": round(time.time(), 1), "action": action, **fields}
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── L1: deterministic build from ingested chunks ─────────────────────────

    def build_l1(self, text_chunks: dict[str, dict], rebuild: bool = False) -> dict:
        """
        Scan every ingested chunk for mentions of other corpus documents:
          - literal filename mentions (extensions discovered from the corpus itself)
          - generic doc-ID tokens resolved through a filename-derived owner index

        Deterministic, no LLM, uses only document content (ground rule 1 safe).
        """
        if (self._data["built_l1"] and not rebuild
                and self._data.get("l1_version") == L1_BUILDER_VERSION):
            return {"skipped": True, "edges_l1": self._count_layer("explicit_ref")}
        if self._data["built_l1"]:
            # stale-version rebuild: drop old explicit_ref layers, keep learned ones
            for key in list(self._data["edges"].keys()):
                edge = self._data["edges"][key]
                edge["layers"].pop("explicit_ref", None)
                if not edge["layers"]:
                    del self._data["edges"][key]

        doc_of = {
            cid: os.path.basename(c.get("file_path", ""))
            for cid, c in text_chunks.items()
            if c.get("file_path")
        }
        basenames = set(doc_of.values())

        # nodes
        for cid, c in text_chunks.items():
            bn = doc_of.get(cid)
            if not bn or bn in self._data["nodes"]:
                continue
            fp = c.get("file_path", "")
            parts = fp.replace("\\", "/").split("/")
            stem, ext = os.path.splitext(bn)
            self._data["nodes"][bn] = {
                "dir": parts[0] if len(parts) > 1 else "",
                "ext": ext.lstrip("."),
                "tokens": [t.lower() for t in re.split(r"[_\-\s\.]+", stem) if len(t) > 2][:12],
                "profile": None,
            }

        # filename-mention regex from extensions actually present in the corpus
        exts = sorted({n["ext"] for n in self._data["nodes"].values() if n["ext"]})
        fname_re = re.compile(r"[\w\-\.]+\.(?:" + "|".join(map(re.escape, exts)) + r")\b") if exts else None

        # doc-ID owner index: an ID token appearing in a filename "belongs" to that
        # file. An ID can name several files (e.g. FA-2245.html and the matching
        # FA-2245 report docx) — keep every owner (bounded) so a mention links to
        # all of them, not just the first scanned.
        id_owner: dict[str, list[str]] = {}
        for bn in sorted(basenames):
            for tok in _ID_TOKEN.findall(bn):
                owners = id_owner.setdefault(tok, [])
                if len(owners) < 3:
                    owners.append(bn)

        mention_counts: dict[str, int] = defaultdict(int)
        for cid, c in text_chunks.items():
            src = doc_of.get(cid)
            if not src:
                continue
            text = c.get("content", "")
            found: set[str] = set()
            if fname_re:
                for m in fname_re.findall(text):
                    if m in basenames and m != src:
                        found.add(m)
            for tok in _ID_TOKEN.findall(text):
                for owner in id_owner.get(tok, ()):
                    if owner != src:
                        found.add(owner)
            for tgt in found:
                mention_counts[_pair_key(src, tgt)] += 1

        for key, cnt in mention_counts.items():
            layers = self._data["edges"].setdefault(key, {"layers": {}, "hits": 0, "last_hit": 0})["layers"]
            layers["explicit_ref"] = {"w": min(1.0, 0.5 + 0.1 * cnt), "mentions": cnt}

        self._data["built_l1"] = True
        self._data["l1_version"] = L1_BUILDER_VERSION
        stats = {"docs": len(self._data["nodes"]), "edges_l1": len(mention_counts)}
        self.log("build_l1", reason="deterministic cross-reference scan of ingested chunks", **stats)
        self.save()
        return stats

    # ── L2: co-retrieval reinforcement (quality-gated) / decay ──────────────

    def reinforce_co_retrieval(self, docs: list[str], quality: float, quality_gate: float = 0.6):
        """Strengthen co-retrieval edges among docs jointly retrieved by a good query."""
        if quality < quality_gate:
            return
        docs = sorted(set(docs))
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                key = _pair_key(docs[i], docs[j])
                edge = self._data["edges"].setdefault(key, {"layers": {}, "hits": 0, "last_hit": 0})
                layer = edge["layers"].setdefault("co_retrieval", {"w": 0.0, "count": 0})
                layer["count"] += 1
                layer["w"] = min(1.0, layer["w"] + 0.1)

    def decay(self, factor: float = 0.95, floor: float = 0.15) -> int:
        """Decay learned layers (L2/L3) of edges never used since last decay; drop dead layers."""
        removed = 0
        for key in list(self._data["edges"].keys()):
            edge = self._data["edges"][key]
            if edge.get("hits", 0) > 0:
                edge["hits"] = 0  # reset usage window
                continue
            for lname in ("co_retrieval", "llm_curated"):
                layer = edge["layers"].get(lname)
                if not layer:
                    continue
                layer["w"] *= factor
                if layer["w"] < floor:
                    del edge["layers"][lname]
                    removed += 1
            if not edge["layers"]:
                del self._data["edges"][key]
        if removed:
            self.log("decay", removed_layers=removed, reason="unused learned edges decayed below floor")
        return removed

    # ── L3: LLM-curated typed edges ──────────────────────────────────────────

    def add_curated_edge(self, a: str, b: str, rel_type: str, evidence_chunks: list[str],
                         rationale: str, weight: float = 0.7):
        key = _pair_key(a, b)
        edge = self._data["edges"].setdefault(key, {"layers": {}, "hits": 0, "last_hit": 0})
        edge["layers"]["llm_curated"] = {
            "w": weight, "type": rel_type,
            "evidence": evidence_chunks[:5], "rationale": rationale[:300],
        }
        self.log("l3_curated_edge", pair=key, rel_type=rel_type,
                 evidence=evidence_chunks[:5], reason=rationale[:300])

    def set_profile(self, doc: str, profile: str):
        if doc in self._data["nodes"]:
            self._data["nodes"][doc]["profile"] = profile[:600]
            self.log("doc_profile", doc=doc, reason="LLM wiki-style doc profile for scope routing")

    # ── traversal ────────────────────────────────────────────────────────────

    @staticmethod
    def _effective_weight(layers: dict) -> float:
        return max((l["w"] * LAYER_FACTOR[n] for n, l in layers.items()), default=0.0)

    def get_neighbors(self, doc: str, top_n: int = 5, min_weight: float = 0.3) -> list[tuple[str, float]]:
        """Neighbors of one doc, best first. Explicit-ref (L1) edges outrank learned
        layers regardless of weight — a document's own cross-references are ground
        truth, while co-retrieval stats are only correlation (v5.2 fix C1: heavily
        reinforced L2 edges were crowding real linked docs out of EXPAND slots).
        Records edge usage."""
        out = []
        for key, edge in self._data["edges"].items():
            a, b = key.split("||", 1)
            if doc not in (a, b):
                continue
            w = self._effective_weight(edge["layers"])
            if w >= min_weight:
                l1 = 1 if "explicit_ref" in edge["layers"] else 0
                out.append((b if a == doc else a, w, key, l1))
        out.sort(key=lambda x: (-x[3], -x[1]))
        now = time.time()
        for _, _, key, _ in out[:top_n]:
            self._data["edges"][key]["hits"] += 1
            self._data["edges"][key]["last_hit"] = now
        return [(d, w) for d, w, _, _ in out[:top_n]]

    def get_neighbors_layered(self, doc: str, top_n: int = 5,
                              min_weight: float = 0.3) -> list[tuple[str, float, bool]]:
        """Like get_neighbors but exposes provenance: (doc, weight, is_explicit_ref).
        Callers that must treat document-declared references differently from
        learned statistical edges (v5.4 expansion tiering) use this."""
        out = []
        for key, edge in self._data["edges"].items():
            a, b = key.split("||", 1)
            if doc not in (a, b):
                continue
            w = self._effective_weight(edge["layers"])
            if w >= min_weight:
                l1 = "explicit_ref" in edge["layers"]
                out.append((b if a == doc else a, w, key, l1))
        out.sort(key=lambda x: (-x[3], -x[1]))
        now = time.time()
        for _, _, key, _ in out[:top_n]:
            self._data["edges"][key]["hits"] += 1
            self._data["edges"][key]["last_hit"] = now
        return [(d, w, l1) for d, w, _, l1 in out[:top_n]]

    def get_scope(self, seeds: list[str], budget: int = 12, min_weight: float = 0.3,
                  learned_min_weight: float = 0.45) -> dict[str, float]:
        """Weighted 1-hop expansion from seed docs → {doc: confidence} capped at budget.

        Learned (L2/L3) edges need a higher bar than explicit refs (v5.7 I2):
        the scope feeds a fusion-wide soft boost, and echo-reinforced co-retrieval
        edges polluting it distorts the base ranking a little more every iteration."""
        scope: dict[str, float] = {}
        for s in seeds:
            if s in self._data["nodes"]:
                scope[s] = max(scope.get(s, 0.0), 1.0)
        for s in seeds:
            for nb, w, is_l1 in self.get_neighbors_layered(s, top_n=6, min_weight=min_weight):
                if not is_l1 and w < learned_min_weight:
                    continue
                scope[nb] = max(scope.get(nb, 0.0), w)
        return dict(sorted(scope.items(), key=lambda kv: -kv[1])[:budget])

    def match_docs_by_tokens(self, query: str, top_n: int = 4, min_overlap: int = 3) -> list[str]:
        """Seed helper: docs whose filename tokens (or ID in the query) match the query."""
        q_ids = set(_ID_TOKEN.findall(query))
        q_words = {w.lower() for w in re.findall(r"[\w가-힣]{3,}", query)}
        scored: list[tuple[str, float]] = []
        for bn, node in self._data["nodes"].items():
            if any(tok in bn for tok in q_ids):
                scored.append((bn, 100.0))
                continue
            overlap = len(q_words & set(node["tokens"]))
            if overlap >= min_overlap:
                scored.append((bn, float(overlap)))
        scored.sort(key=lambda x: -x[1])
        return [bn for bn, _ in scored[:top_n]]

    # ── stats ────────────────────────────────────────────────────────────────

    def _count_layer(self, name: str) -> int:
        return sum(1 for e in self._data["edges"].values() if name in e["layers"])

    @property
    def stats(self) -> dict:
        return {
            "docs": len(self._data["nodes"]),
            "edges": len(self._data["edges"]),
            "l1_explicit": self._count_layer("explicit_ref"),
            "l2_co_retrieval": self._count_layer("co_retrieval"),
            "l3_llm_curated": self._count_layer("llm_curated"),
            "profiles": sum(1 for n in self._data["nodes"].values() if n.get("profile")),
        }
