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
# facet_link (v5.16) sits right under L1: docs that answered DIFFERENT facets of
# one decomposed query are complementarity evidence — the decomposition structure
# itself filters noise (unlike plain co-occurrence), but the signal is still an
# inference (decomposition + retrieval + quality gate), not a document's own claim.
LAYER_FACTOR = {"explicit_ref": 1.0, "facet_link": 0.9, "llm_curated": 0.8,
                "co_retrieval": 0.6}

# Ablation switches (generality experiments): disable evidence layers at runtime.
_NO_L1 = os.environ.get("STRUCTRAG_NO_L1", "") == "1"
_NO_FACET = os.environ.get("STRUCTRAG_NO_FACET", "") == "1"

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

    def _build_nodes(self, text_chunks: dict[str, dict]):
        for cid, c in text_chunks.items():
            bn = os.path.basename(c.get("file_path", "")) if c.get("file_path") else ""
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

    def build_l1(self, text_chunks: dict[str, dict], rebuild: bool = False) -> dict:
        """
        Scan every ingested chunk for mentions of other corpus documents:
          - literal filename mentions (extensions discovered from the corpus itself)
          - generic doc-ID tokens resolved through a filename-derived owner index

        Deterministic, no LLM, uses only document content (ground rule 1 safe).
        """
        if _NO_L1:
            self._data["built_l1"] = True
            self._data["l1_version"] = L1_BUILDER_VERSION
            # nodes are still needed (taxonomy/token matching); only edges skipped
            self._build_nodes(text_chunks)
            self.log("build_l1", reason="STRUCTRAG_NO_L1=1 — ablation run, explicit-ref layer disabled",
                     docs=len(self._data["nodes"]), edges_l1=0)
            self.save()
            return {"docs": len(self._data["nodes"]), "edges_l1": 0, "l1_disabled": True}
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

        self._build_nodes(text_chunks)

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

    def reinforce_co_retrieval(self, docs: list[str], quality: float, quality_gate: float = 0.6,
                               expansion_docs: set[str] | None = None,
                               head_n: int = 10, head_step: float = 0.12,
                               tail_step: float = 0.08):
        """Strengthen co-retrieval edges among jointly OBSERVED docs (G4: full
        observation window, rank-tiered — head pairs earn more per confirmation;
        random tail pairs don't repeat across queries and never clear thresholds,
        sibling-query gold pairs do). Selective echo, generalized: pairs with an
        expansion-injected doc reinforce only when the pair carries explicit_ref
        OR facet_link provenance."""
        if quality < quality_gate:
            return
        expansion_docs = expansion_docs or set()
        ranked = list(dict.fromkeys(d for d in docs if d))
        for i in range(len(ranked)):
            for j in range(i + 1, len(ranked)):
                a, b = ranked[i], ranked[j]
                key = _pair_key(a, b)
                if a in expansion_docs or b in expansion_docs:
                    existing = self._data["edges"].get(key)
                    layers = existing["layers"] if existing else {}
                    if "explicit_ref" not in layers and "facet_link" not in layers:
                        continue
                step = head_step if (i < head_n and j < head_n) else tail_step
                # G5 complementarity filter: lexically-similar pairs (sibling
                # cards share most name tokens) are SIMILARITY, not the
                # complementary links co-retrieval needs ("docs that belong
                # together don't look alike" — dual of D8). Discount them hard.
                ta = set(self._data["nodes"].get(a, {}).get("tokens", ()))
                tb = set(self._data["nodes"].get(b, {}).get("tokens", ()))
                if ta and tb:
                    jac = len(ta & tb) / len(ta | tb)
                    if jac >= 0.5:
                        continue          # near-siblings: no structural credit
                    if jac >= 0.3:
                        step *= 0.4       # related family: slow lane
                edge = self._data["edges"].setdefault(key, {"layers": {}, "hits": 0, "last_hit": 0})
                layer = edge["layers"].setdefault("co_retrieval", {"w": 0.0, "count": 0})
                layer["count"] += 1
                layer["w"] = min(1.0, layer["w"] + step)

    def reinforce_facet_link(self, facet_tops: list[list[str]], quality: float,
                             quality_gate: float = 0.6):
        """v5.16 facet_link: docs that answered DIFFERENT facets (subqueries) of one
        decomposed query get cross-linked — complementarity evidence a user's own
        information need supplied. Only cross-facet pairs (never within a facet:
        those are similarity, not complementarity), only from good retrievals.
        Repeated confirmations climb toward near-L1 effective weight."""
        if _NO_FACET or quality < quality_gate or len(facet_tops) < 2:
            return
        for i in range(len(facet_tops)):
            for j in range(i + 1, len(facet_tops)):
                for a in facet_tops[i]:
                    for b in facet_tops[j]:
                        if not a or not b or a == b:
                            continue
                        key = _pair_key(a, b)
                        edge = self._data["edges"].setdefault(
                            key, {"layers": {}, "hits": 0, "last_hit": 0})
                        layer = edge["layers"].setdefault("facet_link", {"w": 0.0, "count": 0})
                        layer["count"] += 1
                        # v5.18: +0.25/confirm — two confirmations must clear the
                        # expansion threshold (0.5*0.9=0.45 > 0.3); +0.15 left
                        # confirmed links below the bar for entire 5-iter runs
                        layer["w"] = min(1.0, layer["w"] + 0.25)

    def get_escort_neighbor(self, doc: str,
                            prefer: set[str] | None = None) -> tuple[str, float] | None:
        """Best escort candidate for the #1-ranked doc: an explicit-ref neighbor
        first; failing that, a facet_link neighbor confirmed ≥2 times (v5.16).
        v5.18 tie-break: among facet candidates, docs the CURRENT query's own
        facet retrievals surfaced (prefer set) win — cross-evidence between the
        learned graph and this query beats accumulated weight alone."""
        best = {"explicit_ref": None, "facet_link": None, "co_retrieval": None}
        for key, edge in self._data["edges"].items():
            a, b = key.split("||", 1)
            if doc not in (a, b):
                continue
            other = b if a == doc else a
            for lname in ("explicit_ref", "facet_link", "co_retrieval"):
                layer = edge["layers"].get(lname)
                if not layer:
                    continue
                if lname == "facet_link" and layer.get("count", 0) < 2:
                    continue
                if lname == "co_retrieval":
                    if layer["w"] < 0.6 or layer.get("count", 0) < 4:
                        continue
                    ta = set(self._data["nodes"].get(doc, {}).get("tokens", ()))
                    tb = set(self._data["nodes"].get(other, {}).get("tokens", ()))
                    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.3:
                        continue          # similar doc — not an escort target
                bonus = 1.0 if (prefer and other in prefer and lname != "explicit_ref") else 0.0
                cur = best[lname]
                score = layer["w"] + bonus
                if cur is None or score > cur[1]:
                    best[lname] = (other, score)
        return best["explicit_ref"] or best["facet_link"] or best["co_retrieval"]

    def decay(self, factor: float = 0.95, floor: float = 0.15) -> int:
        """Decay learned layers of edges never used since last decay; drop dead layers."""
        removed = 0
        for key in list(self._data["edges"].keys()):
            edge = self._data["edges"][key]
            if edge.get("hits", 0) > 0:
                edge["hits"] = 0  # reset usage window
                continue
            for lname in ("co_retrieval", "llm_curated", "facet_link"):
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
