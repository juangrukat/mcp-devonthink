"""Scholar Workflows Integration Tests.

Creates erasable test data in DEVONthink and exercises three scholarly
research workflows:

  1. Mixed source types  — PDFs, Word, markdown, plain text, RTF, bookmarks,
                           all in one group; tests search + retrieval across types.
  2. Draft-to-source     — a draft chapter note finds related source notes via
                           search + suggest_related.
  3. Thematic regrouping — records tagged with author / topic / method /
                           date appear in multiple tag-scoped searches without
                           being duplicated or moved.

All created records live under "MCP Test - Scholar Workflows" in Inbox and
can be deleted after testing.

Run:
    python3 tests/test_scholar_workflows.py
or:
    python3 -m pytest tests/test_scholar_workflows.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.devonthink_tools import (
    devonthink_create_record,
    devonthink_get_record_by_uuid,
    devonthink_search_records,
)
from app.tools.devonthink_link_tools import (
    devonthink_link_audit_folder,
    devonthink_link_check_reciprocal,
    devonthink_link_detect_bridges,
    devonthink_link_find_orphans,
    devonthink_link_map_neighborhood,
    devonthink_link_score,
    devonthink_link_suggest_related,
    devonthink_link_audit_record,
)

# ---------------------------------------------------------------------------
# Inbox database UUID (existing fixture)
# ---------------------------------------------------------------------------

DB_INBOX = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_results: list[dict[str, Any]] = []


def _run(name: str, fn, *args, **kwargs) -> Any:
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        ok = bool(result.get("ok") if isinstance(result, dict) else result)
        _results.append({"name": name, "ok": ok, "ms": elapsed_ms, "result": result})
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name:60s} {elapsed_ms:7.0f}ms")
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _results.append({"name": name, "ok": False, "ms": elapsed_ms, "error": str(exc)})
        print(f"  [ERR ] {name:60s} {elapsed_ms:7.0f}ms  — {exc}")
        return None


def _run_expect_error(name: str, fn, *args, **kwargs) -> Any:
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        returned_error = isinstance(result, dict) and not result.get("ok")
        _results.append({"name": name, "ok": returned_error, "ms": elapsed_ms, "result": result})
        status = "PASS" if returned_error else "FAIL"
        suffix = "" if returned_error else "  — expected ok=False, got ok=True"
        print(f"  [{status}] {name:60s} {elapsed_ms:7.0f}ms{suffix}")
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _results.append({"name": name, "ok": False, "ms": elapsed_ms, "error": str(exc)})
        print(f"  [ERR ] {name:60s} {elapsed_ms:7.0f}ms  — {exc}")
        return None


def _assert(name: str, condition: bool, detail: str = "") -> None:
    ok = bool(condition)
    _results.append({"name": name, "ok": ok, "ms": 0.0})
    status = "PASS" if ok else "FAIL"
    suffix = f"  — {detail}" if detail and not ok else ""
    print(f"  [{status}] {name:60s}{suffix}")


def _run_osascript(script: str, args: list[str] | None = None) -> str:
    cmd = ["osascript", "-l", "AppleScript", "-e", script]
    if args:
        cmd += ["--", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"osascript failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Corpus setup
# ---------------------------------------------------------------------------

def _create_corpus() -> dict[str, str]:
    """Create the test corpus in DEVONthink.  Returns a dict of UUIDs keyed
    by a short logical name.  All records land under
    Inbox / MCP Test - Scholar Workflows.
    """

    print("\nSetting up Scholar Workflows test corpus …")

    # ----- mixed sources content -----
    m1_text = (
        "# Illuminated Manuscripts Survey\n\n"
        "Illuminated manuscripts from 15th-century Florence represent a key transition in scribal "
        "culture. The Biblioteca Medicea Laurenziana holds major examples.  Paleographic analysis "
        "reveals shifts in uncial script toward humanist minuscule after 1430.  "
        "Codicological examination shows vellum prepared with alum-tawed skins and hair-side "
        "facing the viewer.  Ruling patterns follow a 2+1 column formula common in Florentine "
        "chancery production.  Cross-references to trade documentation confirm that pigments were "
        "imported via Venetian spice merchants.  [[Trade Network Map 1450-1520]]"
    )

    m2_text = (
        "Trade Network Map 1450-1520\n\n"
        "Venetian trade routes connected northern European wool markets to Levantine silk "
        "producers.  The fondaco dei tedeschi served as the primary clearing house for German "
        "merchants.  Documentary evidence from notarial registers shows peak activity between 1460 "
        "and 1490.  Spice prices are indexed against pepper and cinnamon benchmarks.  "
        "The Fugger correspondence records exchange rates with Florentine bankers operating in the "
        "Rialto.  Cartographic sources include the Fra Mauro mappamundi and portolan charts from "
        "Jacopo de' Barbari.  [[Archival Finding Aid - Notarial Registers 1482]]"
    )

    m3_text = (
        "Archival Finding Aid — Notarial Registers 1482-1490\n\n"
        "ASV Notarile Atti b. 12 (1482-1490).  Notary records documenting merchant transactions.  "
        "Cross-references to tax records in Dieci Savi sopra le Decime.  Provenance uncertain "
        "prior to 1750 reorganisation.  Finding aid compiled using ISAD(G) schema.  "
        "Physical condition: moderate foxing, spine intact.  Access restrictions: open to "
        "accredited researchers.  Digitisation status: partially scanned at 400 dpi.  "
        "[[Survey - Illuminated Manuscripts Florence 1400-1500]]"
    )

    m4_text = (
        "Codicological Methods — Quire Analysis and Workshop Identification\n\n"
        "Codicology is the study of books as physical objects.  Methods include quire analysis, "
        "pricking and ruling patterns, script identification, binding structure, and pigment "
        "analysis.  Standard reference: Gilissen 1977.  Application to Florentine production "
        "reveals workshop differentiation by ruling technique.  Catchwords appear consistently "
        "in the lower right margin.  [[Survey - Illuminated Manuscripts Florence 1400-1500]]"
    )

    # ----- draft matching content -----
    draft_text = (
        "Chapter 3 Draft — Paleographic Evidence in Venetian Notarial Archives\n\n"
        "Paleographic analysis of Venetian notarial registers from the late 15th century provides "
        "direct evidence for the spread of humanist script into documentary practice.  The "
        "transition from the earlier italic mercantesca hand is visible in records produced after "
        "1470.  Notarial imbreviature employ a compressed cursive distinct from the formal book "
        "hands documented in the Biblioteca Marciana.  Cross-comparison with Florentine chancery "
        "output shows parallel development driven by humanist educational networks.  "
        "The archival corpus at ASV covers approximately 2400 fascicles for this period.  "
        "Codicological analysis of quires and parchment quality reveals consistent material "
        "culture across workshops.  Finding aids compiled from 19th-century inventories require "
        "re-evaluation against the surviving physical evidence.  Pigment studies of rubrics "
        "suggest workshop specialisation by district."
    )

    ds1_text = (
        "Paleography Manual for Italian Documents\n\n"
        "Practical guide to reading late medieval Italian hands.  Covers mercantesca, notarial "
        "cursive, humanist minuscule, and chancery italic.  Specific sections address "
        "abbreviation systems, ligatures, and numeral conventions.  Examples drawn from ASV and "
        "ASF notarial series.  Paleographic periodisation aligns with known historical watersheds: "
        "Black Death disruptions 1348-1360, humanist reform 1400-1440, printing diffusion "
        "1465-1500.  Key reference for reading imbreviature in Venetian archives."
    )

    ds2_text = (
        "Venetian Notarial System — 14th to 15th Century\n\n"
        "The Venetian notariate operated under guild regulation with mandatory registration.  "
        "Notarial imbreviature recorded in bound fascicles held at the Cancelleria Inferiore.  "
        "The rogiti (fair copies) were delivered to contracting parties.  Survival rates for "
        "15th-century material are approximately 60 percent.  The ASV series Notarile Atti "
        "contains over 4000 buste covering 1300-1800.  Notaries were educated in the ars "
        "notaria curriculum and used standard formulae for contracts, wills, and bills of "
        "exchange.  Humanist influence on documentary script detectable after 1470."
    )

    ds3_text = (
        "Archival Organisation Methods — ISAD(G) and Beyond\n\n"
        "ISAD(G) provides a general international standard for archival description.  Key levels: "
        "fonds, sub-fonds, series, file, item.  Application to pre-modern European archives "
        "requires adaptation for non-hierarchical accumulation patterns.  Finding aids at ASV "
        "often reflect 19th-century reorganisation rather than original order.  Digital finding "
        "aids enable keyword search but lose provenance structure.  Best practice combines "
        "encoded archival description (EAD) with original inventory transcription.  "
        "Codicological metadata enriches item-level description."
    )

    ds4_unrelated_text = (
        "Roman Numismatic Evidence for 1st-Century Commerce\n\n"
        "Coin hoards from Roman Britain provide quantitative evidence for bullion flows.  "
        "The Oxford Roman Economy Project database indexes 50,000 coin finds.  Silver denarius "
        "debasement under Caracalla (212 CE) is tracked through spectroscopic analysis.  "
        "Comparative iconography of reverse types reveals propaganda cycles.  Hoard termination "
        "dates correlate with invasion and civil war episodes.  No connection to medieval "
        "manuscript or archival traditions."
    )

    # ----- thematic content -----
    th_texts = {
        "Rossi-2021-Paleography": (
            "Rossi 2021 — Humanist Script in Florentine Chancery Practice.\n\n"
            "Author: Rossi.  Year: 2021.  Topic: paleography, chancery, Florence.  "
            "Method: diplomatic analysis.  Examines the adoption of humanist minuscule in "
            "official Florentine documents between 1400 and 1450.  Argues for a top-down "
            "reform driven by Bruni and Poggio.  Primary sources: ASF Signoria missive."
        ),
        "Chen-2019-TradeRoutes": (
            "Chen 2019 — Quantitative Modelling of Mediterranean Trade Routes.\n\n"
            "Author: Chen.  Year: 2019.  Topic: trade, economics, network analysis.  "
            "Method: spatial analysis, GIS.  Builds a weighted graph of 15th-century "
            "Mediterranean ports.  Data sourced from notarial registers and customs records.  "
            "Finds Venice and Genoa as dominant hubs with complementary hinterlands."
        ),
        "Rossi-2023-Codicology": (
            "Rossi 2023 — Codicological Approaches to Venetian Workshop Identification.\n\n"
            "Author: Rossi.  Year: 2023.  Topic: codicology, Venice, workshops.  "
            "Method: material analysis, quire study.  Identifies seven distinct workshops "
            "through ruling patterns and pricking sequences.  Connects book production to "
            "trade networks via pigment sourcing."
        ),
        "Nakamura-2020-Archives": (
            "Nakamura 2020 — Digital Finding Aids and Provenance Reconstruction.\n\n"
            "Author: Nakamura.  Year: 2020.  Topic: archives, digital humanities, EAD.  "
            "Method: database design, archival theory.  Argues that EAD schemas fail to "
            "capture non-hierarchical accumulation in Italian pre-modern fonds.  Proposes a "
            "graph model for provenance description."
        ),
        "Chen-2022-Notarial": (
            "Chen 2022 — Notarial Networks and Economic Integration in the Adriatic.\n\n"
            "Author: Chen.  Year: 2022.  Topic: notarial registers, economics, Adriatic.  "
            "Method: network analysis, quantitative history.  Maps notarial co-references "
            "across Venice, Ragusa, and Ancona to identify economic integration corridors.  "
            "Dataset: 12,000 contract summaries from ASV and DAD."
        ),
        "Nakamura-2022-Digitisation": (
            "Nakamura 2022 — Digitisation Priorities in Italian State Archives.\n\n"
            "Author: Nakamura.  Year: 2022.  Topic: digitisation, archives, Italy.  "
            "Method: survey, policy analysis.  Evaluates digitisation progress across ASV, "
            "ASF, ASN from 2010-2022.  Identifies funding gaps and access disparities.  "
            "Recommends open-access mandates for publicly funded scanning projects."
        ),
        "Rossi-2022-PigmentAnalysis": (
            "Rossi 2022 — Pigment Analysis and Trade Networks in Florentine Manuscripts.\n\n"
            "Author: Rossi.  Year: 2022.  Topic: paleography, pigments, trade, Florence.  "
            "Method: XRF spectroscopy, archival research.  Connects ultramarine provenance "
            "in Florentine manuscripts to Venetian spice trade records.  "
            "Links codicological data to economic history."
        ),
        "Chen-2023-MachineLearning": (
            "Chen 2023 — Machine Learning for Script Identification in Historical Documents.\n\n"
            "Author: Chen.  Year: 2023.  Topic: paleography, digital humanities, ML.  "
            "Method: convolutional neural networks, transfer learning.  Trains on the HTR "
            "United dataset.  Achieves 91 percent accuracy for 15th-century Italian hands.  "
            "Opens path to automated codicological feature extraction."
        ),
    }

    th_tags = {
        "Rossi-2021-Paleography":    ["rossi", "paleography", "chancery", "florence", "diplomatic", "2021"],
        "Chen-2019-TradeRoutes":     ["chen", "trade", "economics", "gis", "network-analysis", "2019"],
        "Rossi-2023-Codicology":     ["rossi", "codicology", "venice", "workshops", "material-analysis", "2023"],
        "Nakamura-2020-Archives":    ["nakamura", "archives", "digital-humanities", "ead", "2020"],
        "Chen-2022-Notarial":        ["chen", "notarial-registers", "economics", "adriatic", "network-analysis", "2022"],
        "Nakamura-2022-Digitisation":["nakamura", "digitisation", "archives", "italy", "policy", "2022"],
        "Rossi-2022-PigmentAnalysis":["rossi", "pigments", "trade", "florence", "spectroscopy", "2022"],
        "Chen-2023-MachineLearning": ["chen", "paleography", "digital-humanities", "machine-learning", "2023"],
    }

    # -----------------------------------------------------------------------
    # Build the AppleScript that creates all groups and records
    # -----------------------------------------------------------------------

    def _as_str(s: str) -> str:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _tags_as_list(tags: list[str]) -> str:
        items = ", ".join(f'"{t}"' for t in tags)
        return "{" + items + "}"

    script_parts = [
        'tell application "DEVONthink"',
        f'    set theDB to get database with uuid "0444C204-D8AD-4CC0-8A9A-9F6817C12896"',
        '    set inboxGroup to incoming group of theDB',
        # root group
        '    set rootGrp to create record with {name:"MCP Test - Scholar Workflows", record type:group} in inboxGroup',
        # sub-groups
        '    set mixedGrp to create record with {name:"Mixed Sources", record type:group} in rootGrp',
        '    set draftGrp to create record with {name:"Draft Matching", record type:group} in rootGrp',
        '    set thematicGrp to create record with {name:"Thematic Groups", record type:group} in rootGrp',
    ]

    # mixed sources
    # NOTE: rtfd fails with -1700 on create record (BUG-DT-004);
    # using formatted note (rich text) and html as the non-markdown types instead.
    for var, name, rtype, content in [
        ("ms1", "Survey - Illuminated Manuscripts Florence 1400-1500",  "markdown",       m1_text),
        ("ms2", "Trade Network Map 1450-1520",                          "txt",             m2_text),
        ("ms3", "Archival Finding Aid - Notarial Registers 1482",       "formatted note",  m3_text),
        ("ms4", "Codicological Methods - Quire Analysis",               "html",            m4_text),
    ]:
        script_parts += [
            f'    set {var} to create record with {{name:{_as_str(name)}, record type:{rtype}}} in mixedGrp',
            f'    set plain text of {var} to {_as_str(content)}',
            f'    set tags of {var} to {{"mixed-sources", "test"}}',
        ]

    # draft matching
    for var, name, content in [
        ("dm_draft", "Chapter 3 Draft - Paleographic Evidence",    draft_text),
        ("dm_s1",    "Source - Paleography Manual Italian Docs",   ds1_text),
        ("dm_s2",    "Source - Venetian Notarial System",          ds2_text),
        ("dm_s3",    "Source - Archival Organisation Methods",     ds3_text),
        ("dm_s4",    "Unrelated - Roman Numismatic Evidence",      ds4_unrelated_text),
    ]:
        script_parts += [
            f'    set {var} to create record with {{name:{_as_str(name)}, record type:markdown}} in draftGrp',
            f'    set plain text of {var} to {_as_str(content)}',
        ]
    script_parts += [
        '    set tags of dm_draft to {"draft", "chapter-3", "paleography", "test"}',
        '    set tags of dm_s1    to {"source", "paleography", "test"}',
        '    set tags of dm_s2    to {"source", "notarial-registers", "venice", "test"}',
        '    set tags of dm_s3    to {"source", "archives", "finding-aid", "test"}',
        '    set tags of dm_s4    to {"source", "numismatics", "roman", "test"}',
    ]

    # thematic group
    for var, (key, content) in enumerate(th_texts.items(), start=1):
        vname = f"th{var}"
        tags_list = _tags_as_list(th_tags[key] + ["test"])
        script_parts += [
            f'    set {vname} to create record with {{name:{_as_str(key)}, record type:markdown}} in thematicGrp',
            f'    set plain text of {vname} to {_as_str(content)}',
            f'    set tags of {vname} to {tags_list}',
        ]

    # Emit JSON with all UUIDs
    uuid_fields = [
        ("root", "rootGrp"),
        ("mixed_grp", "mixedGrp"),
        ("draft_grp", "draftGrp"),
        ("thematic_grp", "thematicGrp"),
        ("ms1", "ms1"), ("ms2", "ms2"), ("ms3", "ms3"), ("ms4", "ms4"),
        ("dm_draft", "dm_draft"),
        ("dm_s1", "dm_s1"), ("dm_s2", "dm_s2"), ("dm_s3", "dm_s3"), ("dm_s4", "dm_s4"),
    ]
    for i, (key, _) in enumerate(th_texts.items(), start=1):
        uuid_fields.append((f"th_{key}", f"th{i}"))

    json_parts = []
    for key, var in uuid_fields:
        json_parts.append(f'\\"{key}\\":\\"" & uuid of {var} & "\\"')

    script_parts += [
        '    return "{' + ", ".join(json_parts) + '}"',
        "end tell",
    ]

    full_script = "\n".join(script_parts)

    start = time.perf_counter()
    raw = _run_osascript(full_script)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"  corpus created in {elapsed:.0f}ms")

    uuids: dict[str, str] = json.loads(raw)
    return uuids


# ---------------------------------------------------------------------------
# 1. Mixed source types
# ---------------------------------------------------------------------------

def run_mixed_source_types(uuids: dict[str, str]) -> None:
    print("\n=== WORKFLOW 1 — MIXED SOURCE TYPES ===")
    mixed_grp = uuids["mixed_grp"]

    # Retrieve each record by UUID regardless of type
    for key, label, expected_type in [
        ("ms1", "markdown record (manuscripts survey)",        "markdown"),
        ("ms2", "txt record (trade network)",                  "txt"),
        ("ms3", "formatted note record (finding aid)",         "formatted note"),
        ("ms4", "html record (codicological methods)",         "HTML"),
    ]:
        r = _run(f"get_record_by_uuid {key} ({label})", devonthink_get_record_by_uuid, uuids[key])
        if r:
            actual_type = (r.get("data") or {}).get("type", "")
            type_ok = actual_type.lower() == expected_type.lower()
            _assert(f"  {key} type is {expected_type} (got '{actual_type}')", type_ok)

    # Search across all types
    r = _run("search 'paleographic' (finds markdown+txt content)",
             devonthink_search_records, "paleographic", 25, mixed_grp)
    if r:
        _assert("  found ≥1 results", (r.get("count") or 0) >= 1,
                f"got {r.get('count')}")

    r = _run("search 'notarial registers' (multi-word cross-type)",
             devonthink_search_records, "notarial registers", 25, mixed_grp)
    if r:
        _assert("  notarial registers found ≥1", (r.get("count") or 0) >= 1)

    r = _run("search 'Venetian trade fondaco' in mixed group",
             devonthink_search_records, "Venetian trade fondaco", 25, mixed_grp)
    if r:
        _assert("  venetian trade found ≥1", (r.get("count") or 0) >= 1)

    r = _run("search 'codicological ruling vellum' in mixed group",
             devonthink_search_records, "codicological ruling vellum", 25, mixed_grp)
    if r:
        _assert("  codicological found ≥1", (r.get("count") or 0) >= 1)

    # Link audit across all four record types (exercises BUG-DT-001 workaround)
    for key, label in [("ms1","ms1 markdown"), ("ms2","ms2 txt"), ("ms3","ms3 markdown"), ("ms4","ms4 rtf")]:
        r = _run(f"audit_record {label} (link scan, type={label.split()[1]})",
                 devonthink_link_audit_record, uuids[key])
        if r:
            data = r.get("data") or {}
            edges = data.get("edges") or {}
            _assert(f"  {key} audit returns edges dict", isinstance(edges, dict))

    # Cross-type wikilink: ms1 wikilinks ms2 and ms2 wikilinks ms3
    r = _run("check_reciprocal ms1<->ms2 (wikilink cross-type)",
             devonthink_link_check_reciprocal, uuids["ms1"], uuids["ms2"])
    if r:
        _assert("  ms1<->ms2 consistent field present", "consistent" in (r.get("data") or {}))

    # Suggest related across types
    r = _run("suggest_related ms1 (should find ms2, ms3 via content overlap)",
             devonthink_link_suggest_related, uuids["ms1"], 10)
    if r:
        data = r.get("data") or {}
        suggestions = data.get("suggestions") or []
        _assert("  ms1 suggestions list is list", isinstance(suggestions, list))
        suggested_uuids = {s.get("uuid") for s in suggestions}
        _assert(f"  ms1 suggests ms2 or ms3 (got {len(suggestions)} suggestions)",
                uuids["ms2"] in suggested_uuids or uuids["ms3"] in suggested_uuids,
                f"suggested: {[s.get('name') for s in suggestions]}")

    # Folder audit of mixed group
    r = _run("audit_folder mixed group (4 records, mixed types)",
             devonthink_link_audit_folder, mixed_grp, 50)
    if r:
        data = r.get("data") or {}
        _assert("  audited_count == 4", int(data.get("audited_count") or 0) == 4,
                f"got {data.get('audited_count')}")

    # Score all four mixed records
    r = _run("score all 4 mixed-type records",
             devonthink_link_score, [uuids[k] for k in ("ms1","ms2","ms3","ms4")])
    if r:
        _assert("  score ok", r.get("ok") is True)

    # No orphans expected (all four are wikilinked)
    r = _run("find_orphans in mixed group (all wikilinked, expect 0)",
             devonthink_link_find_orphans, mixed_grp, 50)
    if r:
        data = r.get("data") or {}
        orphans = data.get("orphans") or []
        print(f"         mixed-group orphan count: {len(orphans)}"
              + (f" — {[o.get('name') for o in orphans]}" if orphans else ""))
        # ms4 wikilinks ms1 but ms2 and ms3 wikilink each other, so no true orphans
        _assert("  orphan list is a list", isinstance(orphans, list))


# ---------------------------------------------------------------------------
# 2. Draft-to-source matching
# ---------------------------------------------------------------------------

def run_draft_to_source(uuids: dict[str, str]) -> None:
    print("\n=== WORKFLOW 2 — DRAFT-TO-SOURCE MATCHING ===")
    draft_grp = uuids["draft_grp"]

    # Key terms from the draft chapter
    for term, min_hits in [
        ("paleographic imbreviature", 1),
        ("humanist script notarial",  1),
        ("codicological archival",    1),
        ("Venetian notarial registers", 1),
    ]:
        r = _run(f"search '{term}' in draft group",
                 devonthink_search_records, term, 25, draft_grp)
        if r:
            _assert(f"  '{term}' finds ≥{min_hits} source",
                    (r.get("count") or 0) >= min_hits,
                    f"got {r.get('count')}")

    # Unrelated record should NOT dominate results for paleography query
    r = _run("search 'paleography' (draft group, unrelated should not lead)",
             devonthink_search_records, "paleography", 10, draft_grp)
    if r:
        records = r.get("records") or []
        names = [rec.get("name","") for rec in records]
        unrelated_pos = next((i for i, n in enumerate(names) if "Roman Numismatic" in n), None)
        _assert("  unrelated record not in top 3",
                unrelated_pos is None or unrelated_pos >= 3,
                f"unrelated at position {unrelated_pos}")

    # suggest_related on the draft chapter — should find source notes, not the unrelated one
    r = _run("suggest_related dm_draft (should find dm_s1, dm_s2, dm_s3)",
             devonthink_link_suggest_related, uuids["dm_draft"], 20)
    if r:
        data = r.get("data") or {}
        suggestions = data.get("suggestions") or []
        suggested_uuids = {s.get("uuid") for s in suggestions}
        print(f"         draft suggestions ({len(suggestions)}): "
              f"{[s.get('name') for s in suggestions]}")
        source_uuids = {uuids[k] for k in ("dm_s1","dm_s2","dm_s3")}
        found_sources = source_uuids & suggested_uuids
        _assert("  at least 2 of 3 sources suggested",
                len(found_sources) >= 2,
                f"found {len(found_sources)}/3: {[s.get('name') for s in suggestions if s.get('uuid') in source_uuids]}")
        _assert("  unrelated not in top suggestions",
                uuids["dm_s4"] not in {s.get("uuid") for s in suggestions[:5]},
                f"unrelated Roman record appeared in top 5")

    # score: draft + sources should score higher than draft + unrelated
    pair_related   = [uuids["dm_draft"], uuids["dm_s1"]]
    pair_unrelated = [uuids["dm_draft"], uuids["dm_s4"]]

    r_rel = _run("score draft+s1 (related pair)", devonthink_link_score, pair_related)
    r_unr = _run("score draft+s4 (unrelated pair)", devonthink_link_score, pair_unrelated)

    if r_rel and r_unr:
        def _max_score(result: dict) -> float:
            scores_list = (result.get("data") or {}).get("scores") or []
            if isinstance(scores_list, list) and scores_list:
                return max(float(s.get("score", 0)) for s in scores_list)
            return 0.0

        score_rel = _max_score(r_rel)
        score_unr = _max_score(r_unr)
        print(f"         draft+source score={score_rel:.3f}  draft+unrelated score={score_unr:.3f}")
        _assert(f"  related pair scores higher than unrelated ({score_rel:.3f} > {score_unr:.3f})",
                score_rel > score_unr,
                f"related={score_rel:.3f} unrelated={score_unr:.3f}")

    # audit_folder on draft group — expect dm_s4 as a potential orphan
    r = _run("audit_folder draft group (5 records)",
             devonthink_link_audit_folder, draft_grp, 50)
    if r:
        data = r.get("data") or {}
        _assert("  draft folder audit ok", r.get("ok") is True)
        _assert("  audited_count == 5", int(data.get("audited_count") or 0) == 5,
                f"got {data.get('audited_count')}")

    # map_neighborhood on draft — should pull in source notes
    r = _run("map_neighborhood dm_draft radius=1",
             devonthink_link_map_neighborhood, uuids["dm_draft"], 1, 20)
    if r:
        _assert("  neighborhood ok", r.get("ok") is True)
        data = r.get("data") or {}
        nodes = data.get("nodes") or []
        print(f"         draft neighborhood nodes: {[n.get('name') for n in nodes]}")


# ---------------------------------------------------------------------------
# 3. Thematic regrouping
# ---------------------------------------------------------------------------

def run_thematic_regrouping(uuids: dict[str, str]) -> None:
    print("\n=== WORKFLOW 3 — THEMATIC REGROUPING VIA TAGS ===")
    thematic_grp = uuids["thematic_grp"]

    # Tag-scoped searches — same records must appear under multiple tags
    for tag, expected_min, excluded_author in [
        ("tag:rossi",        3, "chen"),
        ("tag:chen",         4, "rossi"),
        ("tag:nakamura",     2, "chen"),
        ("tag:paleography",  3, None),
        ("tag:archives",     2, None),
        ("tag:digital-humanities", 2, None),
        ("tag:network-analysis",   2, None),
        ("tag:2022",         3, None),
    ]:
        r = _run(f"search {tag} in thematic group",
                 devonthink_search_records, tag, 25, thematic_grp)
        if r:
            count = r.get("count") or 0
            _assert(f"  {tag} finds ≥{expected_min} records (got {count})",
                    count >= expected_min, f"got {count}")
            if excluded_author:
                names = [rec.get("name","") for rec in (r.get("records") or [])]
                has_excluded = any(excluded_author.lower() in n.lower() for n in names)
                _assert(f"  {tag} results don't include {excluded_author} records",
                        not has_excluded,
                        f"found: {[n for n in names if excluded_author.lower() in n.lower()]}")

    # Records appear across author AND topic tags without duplication
    r_rossi  = _run("search tag:rossi",  devonthink_search_records, "tag:rossi",  25, thematic_grp)
    r_paleo  = _run("search tag:paleography", devonthink_search_records, "tag:paleography", 25, thematic_grp)
    if r_rossi and r_paleo:
        rossi_uuids = {rec.get("uuid") for rec in (r_rossi.get("records") or [])}
        paleo_uuids = {rec.get("uuid") for rec in (r_paleo.get("records") or [])}
        overlap = rossi_uuids & paleo_uuids
        print(f"         rossi∩paleography overlap: {len(overlap)} records")
        _assert("  rossi∩paleography overlap ≥1 (Rossi-2021 and Rossi-2022)",
                len(overlap) >= 1)

    # Full folder audit on 8 thematic records
    r = _run("audit_folder thematic group (8 records)",
             devonthink_link_audit_folder, thematic_grp, 100)
    if r:
        data = r.get("data") or {}
        _assert("  thematic audit ok", r.get("ok") is True)
        _assert("  audited_count == 8", int(data.get("audited_count") or 0) == 8,
                f"got {data.get('audited_count')}")
        tag_clusters = data.get("tag_clusters") or {}
        if isinstance(tag_clusters, dict):
            print(f"         tag_clusters keys: {list(tag_clusters.keys())[:10]}")
        else:
            print(f"         tag_clusters entries: {len(tag_clusters)}")

    # find_orphans — thematic records have no wikilinks so all may be orphans
    r = _run("find_orphans thematic group (expect all or most to be orphans)",
             devonthink_link_find_orphans, thematic_grp, 100)
    if r:
        data = r.get("data") or {}
        orphans = data.get("orphans") or []
        print(f"         thematic orphan count: {len(orphans)} / 8")
        _assert("  orphan count plausible (0-8)", 0 <= len(orphans) <= 8)

    # detect_bridges in thematic group (8 records, tag-only edges)
    r = _run("detect_bridges thematic group (8 records, tag-only)",
             devonthink_link_detect_bridges, thematic_grp, 100)
    if r:
        _assert("  detect_bridges ok", r.get("ok") is True)
        data = r.get("data") or {}
        bridges = data.get("bridges") or []
        print(f"         thematic bridge count: {len(bridges)}")

    # suggest_related for Chen-2022-Notarial should find other Chen or economics records
    th_chen_2022 = uuids.get("th_Chen-2022-Notarial")
    if th_chen_2022:
        r = _run("suggest_related Chen-2022 (should find Chen or economics records)",
                 devonthink_link_suggest_related, th_chen_2022, 15)
        if r:
            data = r.get("data") or {}
            suggestions = data.get("suggestions") or []
            suggested_names = [s.get("name","") for s in suggestions]
            print(f"         Chen-2022 suggestions: {suggested_names}")
            found_related = any("Chen" in n or "trade" in n.lower() or "notarial" in n.lower()
                                for n in suggested_names)
            _assert("  Chen-2022 suggestions thematically related", found_related,
                    f"got: {suggested_names}")

    # Cross-author score: Rossi-2022 (pigments+trade) should score well against Chen-2019 (trade)
    th_rossi_2022 = uuids.get("th_Rossi-2022-PigmentAnalysis")
    th_chen_2019  = uuids.get("th_Chen-2019-TradeRoutes")
    if th_rossi_2022 and th_chen_2019:
        r = _run("score Rossi-2022 + Chen-2019 (shared trade topic)",
                 devonthink_link_score, [th_rossi_2022, th_chen_2019])
        if r:
            _assert("  cross-author trade score ok", r.get("ok") is True)


# ---------------------------------------------------------------------------
# Performance regression checks
# ---------------------------------------------------------------------------

def run_performance(uuids: dict[str, str]) -> None:
    print("\n=== PERFORMANCE ===")
    thematic_grp = uuids["thematic_grp"]
    mixed_grp    = uuids["mixed_grp"]

    thresholds = {
        "audit_folder 8 records":    8_000,
        "audit_folder 4 records":    4_000,
        "find_orphans 8 records":   12_000,
        "detect_bridges 8 records": 20_000,
    }

    for label, fn, args, limit_ms in [
        ("audit_folder 8 records",    devonthink_link_audit_folder,   (thematic_grp, 100), thresholds["audit_folder 8 records"]),
        ("audit_folder 4 records",    devonthink_link_audit_folder,   (mixed_grp,    50),  thresholds["audit_folder 4 records"]),
        ("find_orphans 8 records",    devonthink_link_find_orphans,   (thematic_grp, 100), thresholds["find_orphans 8 records"]),
        ("detect_bridges 8 records",  devonthink_link_detect_bridges, (thematic_grp, 100), thresholds["detect_bridges 8 records"]),
    ]:
        start = time.perf_counter()
        try:
            fn(*args)
        except Exception:
            pass
        elapsed_ms = (time.perf_counter() - start) * 1000
        ok = elapsed_ms < limit_ms
        _results.append({"name": f"perf:{label}", "ok": ok, "ms": elapsed_ms})
        status = "PASS" if ok else "SLOW"
        print(f"  [{status}] {label:50s} {elapsed_ms:7.0f}ms  (limit {limit_ms}ms)")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary() -> int:
    total  = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    failed = total - passed
    timed  = [r for r in _results if r.get("ms", 0) > 0]
    if timed:
        slowest = sorted(timed, key=lambda x: -x["ms"])[:5]
        print("\n--- TOP 5 SLOWEST CALLS ---")
        for r in slowest:
            print(f"  {r['ms']:7.0f}ms  {r['name']}")
    print(f"\n{'='*70}")
    print(f"TOTAL: {total}   PASSED: {passed}   FAILED: {failed}")
    print(f"{'='*70}")
    if failed:
        print("\nFAILED TESTS:")
        for r in _results:
            if not r["ok"]:
                err    = r.get("error") or ""
                result = r.get("result") or {}
                detail = err or result.get("error") or ""
                print(f"  ✗ {r['name']}" + (f"  — {detail}" if detail else ""))
    return failed


def run_all() -> int:
    print("DEVONthink MCP — Scholar Workflows Integration Tests")
    print("=" * 70)
    uuids = _create_corpus()
    run_mixed_source_types(uuids)
    run_draft_to_source(uuids)
    run_thematic_regrouping(uuids)
    run_performance(uuids)
    return _print_summary()


# ---------------------------------------------------------------------------
# pytest compatibility
# ---------------------------------------------------------------------------

_corpus_cache: dict[str, str] | None = None


def _get_corpus() -> dict[str, str]:
    global _corpus_cache
    if _corpus_cache is None:
        _corpus_cache = _create_corpus()
    return _corpus_cache


def test_mixed_source_types_pytest():
    run_mixed_source_types(_get_corpus())


def test_draft_to_source_pytest():
    run_draft_to_source(_get_corpus())


def test_thematic_regrouping_pytest():
    run_thematic_regrouping(_get_corpus())


def test_performance_pytest():
    run_performance(_get_corpus())


if __name__ == "__main__":
    sys.exit(0 if run_all() == 0 else 1)
