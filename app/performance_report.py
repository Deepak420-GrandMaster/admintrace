"""How fast Claré actually is, and what the provider is costing us.

    uv run python -m app.performance_report
    uv run python -m app.performance_report --json
    uv run python -m app.performance_report --by provider

Derived from the timings recorded beside every answer, the same way the
production scorecard is derived from source data: nothing here is a number
somebody typed, and an empty report means no answers have been measured yet
rather than that everything is fine.

The targets are product intent, not thresholds that fail a build. A p95 past
its target is worth looking at; a single slow answer is not.
"""

from __future__ import annotations

import argparse
import json

from app.telemetry import TARGET_SECONDS, claim_health, provider_health, summarise, traces


def _bar(value: float, target: float, width: int = 18) -> str:
    if target <= 0:
        return ""
    filled = min(int(round(width * value / target)), width * 2)
    if filled <= width:
        return "▇" * max(filled, 1) + " " * (width - filled)
    return "▇" * width + "▸"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.performance_report",
        description="Answer latency and provider cost, from recorded timings.")
    parser.add_argument("--by", default="response_class",
                        choices=["response_class", "provider", "provider_state",
                                 "evidence_verdict"],
                        help="how to group the latency table")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    rows = traces()
    grouped = summarise(rows, by=args.by)
    health = provider_health(rows)
    claims = claim_health(rows)

    if args.as_json:
        print(json.dumps({"grouped_by": args.by, "latency": grouped,
                          "provider": health, "claims": claims,
                          "targets": TARGET_SECONDS}, indent=2))
        return 0

    print("CLARÉ PERFORMANCE\n")
    if not rows:
        print("  No answers have been timed yet. Ask something and run this again.")
        return 0

    print(f"Latency by {args.by.replace('_', ' ')}")
    print(f"  {'':<18} {'n':>4} {'avg':>7} {'p50':>7} {'p95':>7} {'max':>7}  target")
    for key, stats in grouped.items():
        target = TARGET_SECONDS.get(key.upper())
        flag = ""
        if target is not None:
            flag = f"  {target:.0f}s {'✓' if stats['p95'] <= target else '✗ p95 over'}"
        print(f"  {key:<18} {stats['n']:>4} {stats['avg']:>6.1f}s "
              f"{stats['p50']:>6.1f}s {stats['p95']:>6.1f}s {stats['max']:>6.1f}s{flag}")

    print("\nProvider")
    print(f"  answers measured      {health['answers']}")
    print(f"  rate limited          {health['rate_limit_count']} "
          f"({health['rate_limit_share'] * 100:.0f}%)")
    if health["rate_limit_count"]:
        print(f"  longest wait asked    {health['rate_limit_wait_max']:.0f}s")
    print(f"  answered locally      {health['fallback_count']}")
    print(f"  empty answers         {health['empty_answers']}")

    print("\nClaims")
    if claims["answers_checked"]:
        print(f"  answers checked       {claims['answers_checked']}")
        print(f"  validation p50 / p95  {claims['validation_p50_ms']:.0f} ms / "
              f"{claims['validation_p95_ms']:.0f} ms")
        print(f"  claims kept           {claims['claims_supported']} of "
              f"{claims['claims_generated']} "
              f"({claims['supported_claim_ratio'] * 100:.0f}%)")
        print(f"  contradicted          {claims['claims_contradicted']}")
        print(f"  repair generations    {claims['repair_calls']}")
    else:
        print("  no answers have been claim-checked yet")

    if health["empty_answers"]:
        print("\n  An empty answer is never rendered; it is reported as an "
              "error. Any count here is a provider failure, not a refusal.")
    if health["rate_limit_share"] > 0.2:
        print("\n  The provider is refusing more than one answer in five. "
              "That is a quota ceiling, not a speed problem — check the tier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
