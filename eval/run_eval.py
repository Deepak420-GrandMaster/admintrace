"""Run the bilingual evaluation and write a timestamped result file.

Cross-lingual agreement is reported first and largest, because it measures
whether the central claim holds: that asking in English and asking in French
reach the same official sources. Everything else can look healthy while that
one fails, and if it fails the system is not doing its job.

    uv run python eval/run_eval.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.query.normalize import prepare  # noqa: E402
from app.retrieval import hybrid  # noqa: E402
from app.retrieval.gate import apply_gate, verify_answerable  # noqa: E402

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"
RESULTS = Path(__file__).resolve().parent / "results"
TOP_N = 3


def retrieve(question: str, settings) -> dict:
    prepared = prepare(question, settings)
    ranked = hybrid.search_many(list(prepared.queries), settings=settings)
    decision = verify_answerable(question, apply_gate(ranked, settings), settings)

    fiches = []
    for hit in ranked:
        fiche = hit.metadata.get("fiche_id", "")
        if fiche and fiche not in fiches:
            fiches.append(fiche)

    return {
        "language": prepared.language,
        "search_query": prepared.search_query,
        "glossary_terms": list(prepared.glossary_terms),
        "fiches": fiches,
        "top_fiches": fiches[:TOP_N],
        "best_score": round(decision.best_score, 4),
        "all_scores": [round(h.dense_score, 4) for h in ranked],
        "refused": decision.should_refuse,
        "refused_by": decision.refused_by,
        "passed": len(decision.passed),
    }


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Evaluate En Clair retrieval.").parse_args(argv)

    settings = get_settings()
    spec = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    questions = spec["questions"]

    started = time.time()
    records = []
    for index, question in enumerate(questions, start=1):
        print(f"  [{index:>2}/{len(questions)}] {question['id']}", flush=True)
        records.append({
            "id": question["id"],
            "segment": question["segment"],
            "topic": question.get("topic", ""),
            "expects_refusal": bool(question.get("expect_refusal")),
            "expected_fiche_id": question.get("expected_fiche_id"),
            "en": retrieve(question["en"], settings),
            "fr": retrieve(question["fr"], settings),
        })

    total = len(records)
    exact = [r for r in records if r["en"]["top_fiches"] == r["fr"]["top_fiches"]]
    same_set = [r for r in records
                if set(r["en"]["top_fiches"]) == set(r["fr"]["top_fiches"])]
    top1 = [r for r in records
            if r["en"]["fiches"][:1] == r["fr"]["fiches"][:1] and r["en"]["fiches"]]
    overlaps = [
        len(set(r["en"]["top_fiches"]) & set(r["fr"]["top_fiches"]))
        / max(1, len(set(r["en"]["top_fiches"]) | set(r["fr"]["top_fiches"])))
        for r in records
    ]
    agreement = len(same_set) / total if total else 0.0

    print()
    print("=" * 68)
    print("CROSS-LINGUAL AGREEMENT".center(68))
    print(f"{agreement:.0%}".center(68))
    print("English and French phrasings reaching the same top-3 sources".center(68))
    print("=" * 68)
    print(f"  same top-3, same order : {len(exact)/total:6.1%}  ({len(exact)}/{total})")
    print(f"  same top-3, any order  : {agreement:6.1%}  ({len(same_set)}/{total})")
    print(f"  same top-1 source      : {len(top1)/total:6.1%}  ({len(top1)}/{total})")
    print(f"  mean overlap of top-3  : {statistics.mean(overlaps):6.1%}")

    should_refuse = [r for r in records if r["expects_refusal"]]
    refused_ok = [r for r in should_refuse if r["en"]["refused"] and r["fr"]["refused"]]
    wrongly = [r for r in records if not r["expects_refusal"]
               and (r["en"]["refused"] or r["fr"]["refused"])]

    print("\nREFUSAL CORRECTNESS")
    print(f"  must refuse, did refuse : {len(refused_ok)}/{len(should_refuse)}")
    for record in should_refuse:
        mark = "PASS" if record in refused_ok else "FAIL"
        print(f"    [{mark}] {record['id']}  best en={record['en']['best_score']:.3f} "
              f"fr={record['fr']['best_score']:.3f}  "
              f"(threshold {settings.relevance_threshold})")
    print(f"  answerable but refused  : {len(wrongly)}")

    answerable = [r for r in records if not r["expects_refusal"]]
    scores = sorted(r["en"]["best_score"] for r in answerable)
    print("\nGATE CALIBRATION  (best similarity per question)")
    print(f"  answerable questions : min {scores[0]:.3f}  p10 {scores[len(scores)//10]:.3f}"
          f"  median {statistics.median(scores):.3f}  max {scores[-1]:.3f}")
    for record in should_refuse:
        print(f"  {record['id']} (must refuse) : en {record['en']['best_score']:.3f}  "
              f"fr {record['fr']['best_score']:.3f}")
    separable = should_refuse and max(
        max(r["en"]["best_score"], r["fr"]["best_score"]) for r in should_refuse
    ) < scores[0]
    print(f"  a single threshold separates them: {'yes' if separable else 'NO'}")

    labelled = [r for r in records if r["expected_fiche_id"]]
    print("\nRETRIEVAL HIT RATE")
    if not labelled:
        hit_rate = None
        print(f"  not measurable: no expected_fiche_id filled in ({total} unlabelled).")
    else:
        hits = [r for r in labelled
                if r["expected_fiche_id"] in r["en"]["fiches"]
                and r["expected_fiche_id"] in r["fr"]["fiches"]]
        hit_rate = len(hits) / len(labelled)
        print(f"  expected fiche in top-k, both languages : {hit_rate:.1%} "
              f"({len(hits)}/{len(labelled)})")

    print("\nBY SEGMENT")
    grouped = defaultdict(list)
    for record in records:
        grouped[record["segment"]].append(record)
    print(f"  {'segment':<11}{'n':>4}{'agreement':>12}{'refused':>9}{'mean best':>11}")
    for segment, group in sorted(grouped.items()):
        agree = sum(1 for r in group
                    if set(r["en"]["top_fiches"]) == set(r["fr"]["top_fiches"]))
        refusals = sum(1 for r in group if r["en"]["refused"])
        mean_best = statistics.mean(r["en"]["best_score"] for r in group)
        print(f"  {segment:<11}{len(group):>4}{agree/len(group):>11.0%}"
              f"{refusals:>9}{mean_best:>11.3f}")

    elapsed = time.time() - started
    payload = {
        "run_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(elapsed, 1),
        "settings": {
            "embed_model": settings.embed_model,
            "chat_model": settings.groq_model or settings.ollama_chat_model,
            "provider": settings.llm_provider,
            "retrieval_k": settings.retrieval_k,
            "relevance_threshold": settings.relevance_threshold,
            "feed_version": settings.feed_version,
        },
        "metrics": {
            "cross_lingual_agreement_same_set": round(agreement, 4),
            "cross_lingual_agreement_same_order": round(len(exact) / total, 4),
            "cross_lingual_top1_agreement": round(len(top1) / total, 4),
            "mean_top3_overlap": round(statistics.mean(overlaps), 4),
            "refusal_correct": len(refused_ok),
            "refusal_expected": len(should_refuse),
            "wrongly_refused": len(wrongly),
            "retrieval_hit_rate": hit_rate,
        },
        "records": records,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS / f"eval-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWritten to {path.relative_to(PROJECT_ROOT)}  ({elapsed:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
