"""Persistent local facility jobs over HTTP, using the CLI's solver and QA.

One service worker serializes expensive solves. Accepted bundles are immutable
job outputs; an explicit deterministic cache reuses them only after hash and
independent bundle checks. Review dossiers prepare human decisions, never sign
them. Run one writing service per output directory.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import queue
import re
import threading
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .bundle import write_facility_variant
from .drawing_revision_history import parse_revision_history
from .facility import FacilityProfile, default_facility_profile, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH
from .facility_generation import FACILITY_SOLVER_STRATEGIES, InfeasibleFacilityGeneration, solve_feasible_facility_variants
from .facility_incremental import IncrementalSourceError, re_solve_facility_after_edit
from .io import load_building_result
from .result_geometry import parse_layout_result
from .qa import validate_building_set
from .program_form import ProgramFormError, program_form_config, validate_program_form
from .review import write_review_dossier
from .schema import normalize_building_mapping, validate_mapping


_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}
_FIELDS = {"program", "profile", "variants", "max_attempts", "time_limit_seconds",
           "seed", "equipment_retries", "deterministic_units", "room_solver", "reuse", "revision_history", "incremental_from"}


class JobUnavailable(RuntimeError):
    pass


class _DirectoryLease:
    """OS lock survives stale lock files and is released on process exit."""

    def __init__(self, path):
        self.file = path.open("a+b")
        if path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise ValueError("An active writer already owns this job directory") from exc

    def close(self):
        if not self.file.closed:
            self.file.close()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _save(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    temporary.replace(path)


def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_fingerprint():
    """Cache keys change with executable source, fonts, schemas and versions."""
    digest = hashlib.sha256()
    package = Path(__file__).parent
    paths = [*package.glob("*.py"), *(path for path in package.glob("fonts/*") if path.is_file())]
    for path in sorted(paths):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    for path in sorted(DEFAULT_FACILITY_PROFILE_SCHEMA_PATH.parent.glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for dependency in ("ortools", "shapely", "ezdxf", "reportlab", "pypdf", "ifcopenshell", "jsonschema"):
        digest.update(f"{dependency}={version(dependency)}".encode())
    return digest.hexdigest()


def _validated_request(payload):
    if not isinstance(payload, dict) or set(payload) - _FIELDS or "program" not in payload:
        raise ValueError("Supply program and supported job settings only")
    # Reject non-finite nested values before schema conversion can hide them.
    _json_bytes(payload)
    building = normalize_building_mapping(payload["program"])
    if "profile" in payload:
        issues = validate_mapping(payload["profile"], DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)
        if issues:
            raise ValueError("Invalid facility profile: " + "; ".join(issue.message for issue in issues))
        profile = FacilityProfile.from_mapping(payload["profile"])
    else:
        profile = default_facility_profile()
    settings = {"variants": 1, "max_attempts": None, "time_limit_seconds": 60,
                "seed": 42, "equipment_retries": 2, "deterministic_units": None,
                "room_solver": "auto"}
    settings.update({key: value for key, value in payload.items() if key in settings})
    for key, maximum in (("variants", 20), ("equipment_retries", 20), ("max_attempts", 100)):
        value = settings[key]
        if key == "max_attempts" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{key} must be an integer between 1 and {maximum}")
    if settings["max_attempts"] is not None and settings["max_attempts"] < settings["variants"]:
        raise ValueError("max_attempts must cover the requested variants")
    for key in ("time_limit_seconds", "deterministic_units"):
        value = settings[key]
        if key == "deterministic_units" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
            raise ValueError(f"{key} must be finite and between 0 and 3600")
    if isinstance(settings["seed"], bool) or not isinstance(settings["seed"], int) or not 0 <= settings["seed"] <= 2147483647:
        raise ValueError("seed must be an integer between 0 and 2147483647")
    if settings["room_solver"] not in FACILITY_SOLVER_STRATEGIES:
        raise ValueError("Unknown room_solver")
    reuse = payload.get("reuse", False)
    if not isinstance(reuse, bool) or reuse and settings["deterministic_units"] is None:
        raise ValueError("reuse requires a deterministic_units budget")
    history = parse_revision_history(payload["revision_history"], building=building, drawing=profile.drawing) if "revision_history" in payload else None
    incremental = payload.get("incremental_from")
    if "incremental_from" in payload:
        if (not isinstance(incremental, dict) or set(incremental) != {"job_id", "variant"}
                or not isinstance(incremental.get("job_id"), str) or not _JOB_ID.fullmatch(incremental["job_id"])
                or type(incremental.get("variant")) is not int or not 1 <= incremental["variant"] <= 20):
            raise ValueError("incremental_from requires an accepted job_id and an integer variant between 1 and 20")
    return {"program": building.to_dict(), "profile": profile.to_dict(), "settings": settings, "reuse": reuse,
            **({"revision_history": history.to_input()} if history is not None else {}),
            **({"incremental_from": dict(incremental)} if incremental is not None else {})}


class FacilityService:
    def __init__(self, directory, *, start_worker=True):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lease = _DirectoryLease(self.directory / ".service.lock") if start_worker else None
        self.lock = threading.RLock()
        self.closing = threading.Event()
        self.jobs = {}
        self.queue = queue.Queue()
        self.fingerprint = _runtime_fingerprint()
        for path in sorted(self.directory.glob("*/job.json")):
            if not _JOB_ID.fullmatch(path.parent.name):
                continue
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("id") != path.parent.name:
                raise ValueError(f"Job identity mismatch in {path}")
            if start_worker and job["status"] not in _TERMINAL:
                job["status"] = "INTERRUPTED"
                job["error"] = {"message": "Service restarted before this job completed; submit again"}
                _save(path, job)
            self.jobs[job["id"]] = job
        self.worker = None
        if start_worker:
            self.worker = threading.Thread(target=self._work, name="facility-jobs", daemon=True)
            self.worker.start()

    def close(self):
        if self.worker:
            self.closing.set()
            self.queue.put(None)
            # An active CP-SAT call finishes its current bounded solve. Keep
            # the writer lock until the worker actually exits; shutdown does
            # not wait through every queued solve.
            self.worker.join(timeout=5)
            if not self.worker.is_alive():
                self.worker = None

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["status"] not in _TERMINAL:
                self._update(job_id, status="CANCELLED" if job["status"] == "QUEUED" else "CANCEL_REQUESTED")
            return self.get(job_id)

    def get(self, job_id):
        with self.lock:
            if not _JOB_ID.fullmatch(job_id) or job_id not in self.jobs:
                raise KeyError(job_id)
            return json.loads(_json_bytes(self.jobs[job_id]))

    def list(self):
        with self.lock:
            return [self.get(job_id) for job_id in sorted(self.jobs)]

    def submit(self, payload):
        return self._submit_validated(self._resolve_incremental_source(_validated_request(payload)))

    def submit_batch(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"jobs"} or not isinstance(payload["jobs"], list) or not 1 <= len(payload["jobs"]) <= 32:
            raise ValueError("Supply a jobs array with 1 to 32 requests")
        requests = [self._resolve_incremental_source(_validated_request(item)) for item in payload["jobs"]]
        return [self._submit_validated(item) for item in requests]

    def _resolve_incremental_source(self, request):
        """Snapshot verified accepted source semantics before queue/cache creation."""
        if "incremental_from" not in request:
            return request
        reference = request["incremental_from"]
        job = self._accepted(reference["job_id"])
        if not self._hashes_match(job) or not self.check(job["id"])["ok"]:
            raise JobUnavailable("Incremental source job changed or no longer passes independent QA")
        path = self.artifact(job["id"], f"building_{reference['variant']:02d}.json")
        source_hash = _digest(path)
        prior_building, prior_result, _ = load_building_result(path)
        if _digest(path) != source_hash:
            raise JobUnavailable("Incremental source changed while it was loaded")
        return {**request, "incremental_source": {"job_id": job["id"], "variant": reference["variant"],
                "file_sha256": source_hash, "program": prior_building.to_dict(), "layout": prior_result.to_dict()}}

    def _ensure_incremental_source(self, source):
        try:
            job = self._accepted(source["job_id"])
            path = self.artifact(job["id"], f"building_{source['variant']:02d}.json")
            if not self._hashes_match(job) or _digest(path) != source["file_sha256"]:
                raise JobUnavailable("Incremental source artifact hashes changed")
        except (JobUnavailable, KeyError, OSError, ValueError) as exc:
            raise IncrementalSourceError(str(exc), {
                "stage": "input", "status": "FAIL", "code": "source_job_changed",
                "source_job_id": source["job_id"], "source_variant": source["variant"],
                "source_file_sha256": source["file_sha256"], "proof_scope": "supplied_incremental_source",
                "proven_building_infeasible": False,
            }) from exc

    def _solve_incremental(self, request, building, profile):
        source = request["incremental_source"]
        self._ensure_incremental_source(source)
        identity = {"source_job_id": source["job_id"], "source_variant": source["variant"],
                    "source_file_sha256": source["file_sha256"]}
        try:
            generation = re_solve_facility_after_edit(
                normalize_building_mapping(source["program"]), parse_layout_result(source["layout"]),
                building, profile, cache_cluster_shapes=True, **request["settings"])
        except IncrementalSourceError as exc:
            exc.evidence.update(identity)
            raise
        except InfeasibleFacilityGeneration as exc:
            evidence = {**exc.generation.incremental_evidence, **identity}
            raise InfeasibleFacilityGeneration(str(exc), replace(exc.generation, incremental_evidence=evidence)) from exc
        self._ensure_incremental_source(source)
        return replace(generation, incremental_evidence={**generation.incremental_evidence, **identity})

    def _submit_validated(self, request):
        if self.closing.is_set():
            raise JobUnavailable("Service is shutting down")
        cache_key = hashlib.sha256(_json_bytes({"runtime": self.fingerprint,
                                              **{key: value for key, value in request.items() if key != "reuse"}})).hexdigest()
        with self.lock:
            if request["reuse"]:
                for previous in self.jobs.values():
                    if previous["status"] == "SUCCEEDED" and previous["cache_key"] == cache_key:
                        try:
                            if self._hashes_match(previous) and self.check(previous["id"])["ok"]:
                                return {**self.get(previous["id"]), "cache_hit": True}
                        except (OSError, ValueError, RuntimeError):
                            pass
            job_id = uuid.uuid4().hex
            root = self.directory / job_id
            root.mkdir()
            job = {"id": job_id, "status": "QUEUED", "cache_key": cache_key,
                   "runtime": self.fingerprint, "settings": request["settings"],
                   "expert_acceptance": "PENDING", "cache_hit": False}
            _save(root / "request.json", request)
            _save(root / "job.json", job)
            self.jobs[job_id] = job
            self.queue.put(job_id)
            return self.get(job_id)

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            _save(self.directory / job_id / "job.json", self.jobs[job_id])

    def _work(self):
        try:
            while True:
                job_id = self.queue.get()
                try:
                    if job_id is None or self.closing.is_set():
                        return
                    self._run(job_id)
                finally:
                    self.queue.task_done()
        finally:
            if self.lease:
                self.lease.close()

    def _run(self, job_id):
        root = self.directory / job_id
        with self.lock:
            if self.jobs[job_id]["status"] == "CANCELLED":
                return
            self._update(job_id, status="RUNNING")
        try:
            request = json.loads((root / "request.json").read_text(encoding="utf-8"))
            building = normalize_building_mapping(request["program"])
            profile = FacilityProfile.from_mapping(request["profile"])
            generation = (self._solve_incremental(request, building, profile) if "incremental_source" in request
                          else solve_feasible_facility_variants(building, profile, **request["settings"]))
            prepared = building.with_required_flow_opening_width()
            manifest = {"project": prepared.layout.project_name, "profile": profile.to_dict(),
                        "generation": generation.to_dict(), "variants": []}
            for candidate in generation.accepted:
                variant_evidence = candidate.generation_evidence()
                if generation.incremental_evidence is not None:
                    variant_evidence.update(incremental_evidence=generation.incremental_evidence,
                                            room_solver_evidence=generation.room_solver_evidence)
                manifest["variants"].append(write_facility_variant(
                    root, prepared, profile, candidate.result,
                    layout_report=candidate.layout_report, equipment=candidate.equipment,
                    equipment_report=candidate.equipment_report, flow_routes=candidate.flow_routes,
                    flow_report=candidate.flow_report, facility_report=candidate.facility_report,
                    generation_evidence=variant_evidence,
                    **({"revision_history": request["revision_history"]} if "revision_history" in request else {})))
                write_review_dossier(root / "review", prepared, candidate.result, candidate.equipment,
                                     candidate.flow_routes, candidate.facility_report, profile,
                                     source=root / f"building_{candidate.result.variant:02d}.json")
            _save(root / "manifest.json", manifest)
            qa = validate_building_set(root, expected_variants=request["settings"]["variants"], profile=profile).to_dict()
            _save(root / "qa-report.json", qa)
            if not qa["ok"]:
                raise RuntimeError("Generated bundle failed independent QA; see qa-report.json")
            if "incremental_source" in request:
                self._ensure_incremental_source(request["incremental_source"])
            hashes = {path.relative_to(root).as_posix(): _digest(path)
                      for path in root.rglob("*") if path.is_file() and path.name not in ("job.json", "request.json")}
            with self.lock:
                if self.jobs[job_id]["status"] == "CANCEL_REQUESTED":
                    self._update(job_id, status="CANCELLED")
                else:
                    self._update(job_id, status="SUCCEEDED", qa=qa, artifact_hashes=hashes)
        except Exception as exc:
            error = {"message": str(exc), "type": type(exc).__name__}
            if isinstance(exc, InfeasibleFacilityGeneration):
                error["generation"] = exc.generation.to_dict()
            if isinstance(exc, IncrementalSourceError):
                error["input_evidence"] = exc.evidence
            _save(root / "generation-failure.json", error)
            with self.lock:
                status = "CANCELLED" if self.jobs[job_id]["status"] == "CANCEL_REQUESTED" else "FAILED"
                self._update(job_id, status=status, error=error)

    def _accepted(self, job_id):
        job = self.get(job_id)
        if job["status"] != "SUCCEEDED":
            raise JobUnavailable(f"Job is {job['status']}; no accepted artifacts are published")
        return job

    def _hashes_match(self, job):
        root = self.directory / job["id"]
        return all((root / name).is_file() and _digest(root / name) == digest
                   for name, digest in job.get("artifact_hashes", {}).items())

    def artifacts(self, job_id, *, review=False):
        job = self._accepted(job_id)
        return sorted(name for name in job["artifact_hashes"] if name.startswith("review/") == review)

    def artifact(self, job_id, name):
        job = self._accepted(job_id)
        root = (self.directory / job_id).resolve()
        path = (root / name).resolve()
        if name not in job["artifact_hashes"] or not path.is_relative_to(root) or not path.is_file():
            raise KeyError(name)
        if _digest(path) != job["artifact_hashes"][name]:
            raise JobUnavailable("Artifact changed after its accepted checkpoint")
        return path

    def check(self, job_id):
        self._accepted(job_id)
        root = self.directory / job_id
        request = json.loads((root / "request.json").read_text(encoding="utf-8"))
        return validate_building_set(root, expected_variants=request["settings"]["variants"],
                                    profile=FacilityProfile.from_mapping(request["profile"])).to_dict()


def create_api_server(directory, address=("127.0.0.1", 0), *, token=None):
    try:
        local = ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        local = address[0] == "localhost"
    if not local and not token:
        raise ValueError("A non-loopback API requires an explicit bearer token")
    if token is not None and (not isinstance(token, str) or not token.strip()):
        raise ValueError("API token must be a non-empty string")
    service = FacilityService(directory)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _json(self, status, payload):
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self._json(401, {"error": "Bearer token required"})
                return False
            return True

        def _payload(self):
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("JSON body must contain between 1 and 2000000 bytes")
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"Duplicate JSON key: {key}")
                    result[key] = value
                return result
            return json.loads(self.rfile.read(length), object_pairs_hook=unique_object,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON value: {value}")))

        def do_GET(self):
            self._dispatch(False)

        def do_POST(self):
            self._dispatch(True)

        def _dispatch(self, post):
            route = unquote(urlsplit(self.path).path)
            static = {"/": ("program.html", "text/html; charset=utf-8"),
                      "/program.js": ("program.js", "text/javascript; charset=utf-8"),
                      "/program.css": ("program.css", "text/css; charset=utf-8"),
                      "/favicon.ico": (None, "image/x-icon")}
            if not post and route in static:
                name, content_type = static[route]
                body = files("layout_configurator").joinpath("static", name).read_bytes() if name else b""
                self.send_response(200 if name else 204)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
            if not self._authorized():
                return
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            try:
                if parts == ["program", "config"] and not post:
                    return self._json(200, program_form_config())
                if parts == ["program", "validate"] and post:
                    return self._json(200, validate_program_form(self._payload()))
                if parts == ["health"] and not post:
                    return self._json(200, {"status": "ok", "runtime": service.fingerprint})
                if parts == ["jobs"]:
                    if not post:
                        return self._json(200, {"jobs": service.list()})
                    job = service.submit(self._payload())
                    return self._json(200 if job["cache_hit"] else 202, job)
                if parts == ["jobs", "batch"] and post:
                    return self._json(202, {"jobs": service.submit_batch(self._payload())})
                if len(parts) >= 2 and parts[0] == "jobs":
                    job_id = parts[1]
                    service.get(job_id)
                    if len(parts) == 2 and not post:
                        return self._json(200, service.get(job_id))
                    if parts[2:] == ["check"] and post:
                        if self._payload() != {}:
                            raise ValueError("check accepts an empty JSON object")
                        return self._json(200, service.check(job_id))
                    if parts[2:] == ["cancel"] and post:
                        if self._payload() != {}:
                            raise ValueError("cancel accepts an empty JSON object")
                        return self._json(200, service.cancel(job_id))
                    if parts[2:] in (["artifacts"], ["review"]) and not post:
                        review = parts[2] == "review"
                        return self._json(200, {"files": service.artifacts(job_id, review=review), "expert_acceptance": "PENDING"})
                    if len(parts) >= 4 and parts[2] == "artifacts" and not post:
                        path = service.artifact(job_id, "/".join(parts[3:]))
                        body = path.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        return self.wfile.write(body)
                self._json(404, {"error": "Unknown route"})
            except ProgramFormError as exc:
                self._json(400, {"error": str(exc), "issues": exc.issues})
            except KeyError:
                self._json(404, {"error": "Unknown job or artifact"})
            except JobUnavailable as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, TypeError, UnicodeError) as exc:
                self._json(400, {"error": str(exc)})
            except (OSError, RuntimeError) as exc:
                self._json(500, {"error": str(exc)})

    class ApiServer(ThreadingHTTPServer):
        def server_close(self):
            super().server_close()
            service.close()

    try:
        server = ApiServer(address, Handler)
    except Exception:
        service.close()
        raise
    server.service = service
    return server


def serve_api(directory, *, host="127.0.0.1", port=8766, token=None):
    server = create_api_server(directory, (host, port), token=token)
    print(f"Facility API: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
