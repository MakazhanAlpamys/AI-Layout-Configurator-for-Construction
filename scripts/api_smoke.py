"""Exercise a deployed API with the canonical pharma pilot and retain evidence."""

import argparse
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

from layout_configurator.facility import load_facility_profile
from layout_configurator.io import load_building


def _wait_for_health(request, *, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return request("/health")
        except (URLError, ConnectionError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--program", type=Path, default=Path("examples/pharma_cleanroom_pilot.yaml"))
    parser.add_argument("--profile", type=Path, default=Path("rules/pharma_cleanroom_pilot.yaml"))
    parser.add_argument("--output", type=Path, default=Path("out/api-smoke/report.json"))
    parser.add_argument("--timeout", type=float, default=360)
    args = parser.parse_args()
    token = os.environ.get("LAYOUT_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def request(path, payload=None, *, binary=False):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        current_headers = headers if data is None else {**headers, "Content-Type": "application/json"}
        with urlopen(Request(args.url.rstrip("/") + path, data=data, headers=current_headers), timeout=30) as response:
            body = response.read()
        return body if binary else json.loads(body)

    health = _wait_for_health(request)
    started = time.monotonic()
    job = request("/jobs", {
        "program": load_building(args.program).to_dict(),
        "profile": load_facility_profile(args.profile).to_dict(),
        "variants": 1, "max_attempts": 3, "time_limit_seconds": 30, "seed": 1,
    })
    job_id = job["id"]
    while job["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
        if time.monotonic() - started > args.timeout:
            raise RuntimeError(f"API pilot exceeded {args.timeout}s: {job_id}")
        time.sleep(0.25)
        job = request(f"/jobs/{job_id}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"job": job, "health": health, "elapsed_seconds": time.monotonic() - started}
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if job["status"] != "SUCCEEDED":
        raise RuntimeError(f"API pilot failed; evidence retained at {args.output}: {job.get('error')}")
    qa = request(f"/jobs/{job_id}/check", {})
    if not qa["ok"]:
        raise RuntimeError("Independent API bundle QA failed")
    files = request(f"/jobs/{job_id}/artifacts")["files"]
    for suffix in (".json", ".dxf", ".pdf", ".ifc", ".bcf"):
        if "building_01" + suffix not in files:
            raise RuntimeError(f"Missing accepted pilot artifact: {suffix}")
    pdf = request(f"/jobs/{job_id}/artifacts/building_01.pdf", binary=True)
    if not pdf.startswith(b"%PDF-"):
        raise RuntimeError("Downloaded PDF has an invalid signature")
    review = request(f"/jobs/{job_id}/review")
    if review["expert_acceptance"] != "PENDING" or len([name for name in review["files"] if name.endswith(".md")]) != 3:
        raise RuntimeError("Expected three unsigned expert review dossiers")
    evidence.update(qa=qa, artifacts=files, review=review)
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"job_id": job_id, "status": job["status"], "qa_ok": qa["ok"],
                      "expert_acceptance": review["expert_acceptance"],
                      "elapsed_seconds": round(evidence["elapsed_seconds"], 2),
                      "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
