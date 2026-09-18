"""POST every public sample case to a running GridWise instance, compare directive_interpretation
against the reference semantics, replay the returned plan, and report cost ratio vs. the reference.

Usage: python scripts/run_samples.py [base_url]   (default base_url: http://localhost:8000)
"""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import OptimizeRequest, DirectiveInterpretation, HourPlan
from app.validator import replay

SAMPLES = Path(__file__).resolve().parent.parent / "Problem_doc" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def note_matches(got: dict, exp: dict) -> bool:
    if got["directive_type"] != exp["directive_type"] or got["applies"] != exp["applies"]:
        return False
    g, e = got.get("structured_adjustment"), exp.get("structured_adjustment")
    if e is None:
        return g is None
    if g is None or g["hours"] != e["hours"]:
        return False
    return all(abs(g[k] - v) <= 0.01 for k, v in e.items() if k != "hours")


def main() -> int:
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
    cases = json.loads(SAMPLES.read_text())["cases"]

    ok = 0
    with httpx.Client(timeout=30.0) as client:
        for case in cases:
            cid = case["id"]
            req = OptimizeRequest(**case["input"])
            resp = client.post(f"{base_url}/optimize-energy", json=case["input"])
            if resp.status_code != 200:
                print(f"{cid:12s} FAIL   http={resp.status_code} body={resp.text[:200]}")
                continue
            body = resp.json()

            exp = case["expected_output"]
            interp_ok = all(
                note_matches(got, e)
                for got, e in zip(body["directive_interpretation"], exp["directive_interpretation"])
            )

            directives = [DirectiveInterpretation(**d) for d in body["directive_interpretation"]]
            plan = [HourPlan(**p) for p in body["hourly_plan"]]
            errs = replay(req, directives, plan)

            team_cost = body["total_cost_bdt"]
            exp_cost = exp["total_cost_bdt"]
            ratio = team_cost / exp_cost if exp_cost else float("nan")

            status = "OK" if interp_ok and not errs else "MISMATCH"
            ok += status == "OK"
            print(f"{cid:12s} {status:9s} interp_ok={interp_ok} replay_errs={len(errs)} "
                  f"cost={team_cost:.2f} expected={exp_cost:.2f} ratio={ratio:.4f}")
            for e in errs[:3]:
                print(f"             - {e}")

    print(f"\n{ok}/{len(cases)} cases passed")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
