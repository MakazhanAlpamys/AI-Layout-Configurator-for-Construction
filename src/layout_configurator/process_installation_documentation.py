"""Explicit version 0.2 process/path DRAFTs with independent saved-PDF readback.

Only fresh admitted 0/90 poses, supported axis sweeps and bound exterior
inventory are drawn. Local two-dimensional evidence never approves complete
installation, three-dimensional feasibility or engineering acceptance.
"""

from dataclasses import asdict, fields
import math
from pathlib import Path

from . import process_documentation as _document
from .orthogonal_bundle import _publish_directory
from .process_documentation import ProcessDocumentationLimits
from .process_installation_review import (ProcessInstallationPathLimits,
                                           load_process_review_with_paths)


_SCOPE = "Synchronized supplied process appendix, admitted installation poses and supported axis sweeps"
_PLAN_BOUNDS = (50.0, 185.0, 825.0, 695.0)
_PATH_STYLES = {
    "installation_staging_floor": {"stroke": (.35, .42, .45), "fill": _document.INK,
        "paint": "S", "width": .7, "layer": 3},
    "installation_staging_obstacle": {"stroke": (.60, .15, .45), "fill": _document.INK,
        "paint": "S", "width": 1.1, "layer": 3},
    "installation_sweep_body": {"stroke": (.17, .28, .72), "fill": _document.INK,
        "paint": "S", "width": .7, "layer": 4},
    "installation_sweep_clearance": {"stroke": (.78, .33, .04), "fill": _document.INK,
        "paint": "S", "width": .7, "layer": 4, "dash": (3, 2)},
    "installation_pose_body": {"stroke": (.12, .22, .65), "fill": _document.INK,
        "paint": "S", "width": 1.3, "layer": 5},
    "installation_pose_clearance": {"stroke": (.78, .33, .04), "fill": _document.INK,
        "paint": "S", "width": 1.0, "layer": 5, "dash": (3, 2)},
}


def _valid_path_limits(limits):
    defaults = ProcessInstallationPathLimits()
    if type(limits) is not ProcessInstallationPathLimits or any(type(getattr(limits, row.name)) is not int or
            not 1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults)):
        raise ValueError("Installation document path limits require bounded actual positive integers")


def _admit_review(review):
    section = review.get("installation_paths")
    if type(section) is not dict or not _document._same(section.get("identity"), review["identity"]) or \
            type(section.get("source_requests")) is not list or type(section.get("reports")) is not list or \
            section.get("request_set_sha256") != _document._sha(_document._json(section["source_requests"])):
        raise ValueError("Explicit process installation documents require a complete version 0.2 request inventory")
    requests = section["source_requests"]
    reports = section["reports"]
    by_id = {row["id"]: row for row in requests}
    if len(reports) != len(requests) or len(by_id) != len(requests) or \
            {row["request_id"] for row in reports} != set(by_id):
        raise ValueError("Installation request and fresh report inventories differ")
    for wrapper in reports:
        request = by_id[wrapper["request_id"]]
        if wrapper["request_id"] != request["id"] or wrapper["request_sha256"] != _document._sha(_document._json(request)) or \
                not _document._same(wrapper["identity"], review["identity"]):
            raise ValueError("Installation evidence lost its original request or common component identity")
        evidence = wrapper["evidence"]
        if type(evidence.get("admitted_path_geometry")) is not dict or \
                set(evidence["admitted_path_geometry"]) != {"transport_clearance", "exterior_staging"} or \
                type(evidence.get("pose_validation")) is not list or type(evidence.get("segments")) is not list or \
                len(evidence["pose_validation"]) != len(request["poses"]) or len(evidence["segments"]) != len(request["poses"]) - 1:
            raise ValueError("Installation geometry is incomplete or was not independently admitted")


def _sections(review):
    sections = _document._content_sections(review)
    section = review["installation_paths"]
    lines = ["Installation report status: " + section["status"],
        "Request set SHA256: " + section["request_set_sha256"],
        "Requested targets: " + _document._json(section["requested_targets"]).decode("utf-8"),
        "Covered targets: " + _document._json(section["covered_targets"]).decode("utf-8"),
        "Missing targets: " + _document._json(section["missing_targets"]).decode("utf-8"),
        "Supplied requests: " + _document._json(section["source_requests"]).decode("utf-8"),
        "Only freshly admitted literal poses and supported axis translations are painted.",
        "Bound staging covers supplied two-dimensional inventory only; unbound inventory is not painted.",
        "Local evidence does not establish full installation, three-dimensional feasibility or engineering acceptance."]
    sections.append(("installation_coverage", None, "Supplied installation coverage and source requests", lines))
    for wrapper in section["reports"]:
        lines = ["Request ID: " + wrapper["request_id"], "Request SHA256: " + wrapper["request_sha256"],
            "Report status: " + wrapper["status"],
            "Fresh installation evidence: " + _document._json(wrapper["evidence"]).decode("utf-8")]
        sections.append(("installation_path", wrapper["request_id"], "Fresh supplied installation path: " + wrapper["request_id"], lines))
    return sections


def _legend(review):
    lines = ["Body: admitted poses and axis sweeps", "Clearance: separate bound transport geometry",
        "Staging: admitted source inventory only", "Unsupported motion: no inferred sweep",
        "Full installation and three-dimensional feasibility: UNKNOWN"]
    for number, wrapper in enumerate(review["installation_paths"]["reports"], 1):
        lines.append(f"Path {number}: {wrapper['request_sha256']} | {wrapper['status']} | "
                     f"2D {wrapper['evidence']['local_2d_path_status']}")
    return lines


def _check_appendix(review, body):
    for kind, identifier, _, lines in _sections(review):
        if kind.startswith("installation_") and not _document._inventory_words_match("\n".join(body[(kind, identifier)]), lines):
            raise ValueError("Printed installation request, source identity, scope, verdict or witness differs from fresh evidence")
    if not _document._inventory_words_match("\n".join(body[("plan", "source_overlays")]), _legend(review)):
        raise ValueError("Installation overlay omitted or changed its particular request SHA and fresh path verdict")


def _rectangle(x, y, width, depth):
    values = (x, y, width, depth)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values) or width <= 0 or depth <= 0 or \
            not math.isfinite(x + width) or not math.isfinite(y + depth) or x + width <= x or y + depth <= y:
        raise ValueError("Admitted installation document rectangle is not representable")
    return [[x, y], [x + width, y], [x + width, y + depth], [x, y + depth]]


def _literal_shape(value):
    if value["kind"] == "rectangle":
        return _rectangle(value["x"], value["y"], value["width"], value["depth"])
    if value["kind"] == "orthogonal_polygon":
        points = [list(point) for point in value["points"]]
        return points[:-1] if points and points[0] == points[-1] else points
    raise ValueError("Only freshly admitted literal installation geometry can be painted")


def _path_records(review, limits):
    # An explicit closing endpoint is redundant with the PDF close-path
    # operator. Trim it only in this detached drawing inventory; original
    # source frames and their signatures remain exact, including that point.
    original = [(kind, identifier, points[:-1] if closed and len(points) > 1 and points[0] == points[-1] else points, closed)
                for kind, identifier, points, closed in _document._source_paths(review, limits)]
    staging, sweeps, poses = [], [], []
    for wrapper in review["installation_paths"]["reports"]:
        digest, evidence = wrapper["request_sha256"], wrapper["evidence"]
        admitted = evidence["admitted_path_geometry"]
        value = admitted["exterior_staging"]
        if value is not None:
            staging.append(("installation_staging_floor", digest + ":staging", _literal_shape(value["floor"]), True))
            for number, obstacle in enumerate(value["obstacles"]):
                staging.append(("installation_staging_obstacle", f"{digest}:obstacle:{number}", _literal_shape(obstacle), True))
        for namespace, field, output in (("segments", "sweeps", sweeps), ("pose_validation", "geometry", poses)):
            for row in evidence[namespace]:
                for geometry in row[field]:
                    role = geometry["role"]
                    if role not in ("body", "transport_clearance") or role == "transport_clearance" and admitted["transport_clearance"] is None:
                        raise ValueError("A painted installation contour lacks its separately admitted geometry authority")
                    kind = "sweep" if namespace == "segments" else "pose"
                    tag = "installation_" + kind + ("_body" if role == "body" else "_clearance")
                    identifier = f"{digest}:{'segment' if kind == 'sweep' else 'pose'}:{row['index']}"
                    points = _rectangle(*(geometry[name] for name in ("x_mm", "y_mm", "width_mm", "depth_mm")))
                    output.append((tag, identifier, points, True))
    # Unfilled path contours remain above original room material. No supplied
    # endpoint pair is used to manufacture a missing or unsupported sweep.
    return _document._admit_source_paths(original + staging + sweeps + poses, limits)


def _projection(review, limits):
    paths = _path_records(review, limits)
    layout = review["source_frames"]["building"]["layout"]
    boundary, half = layout["boundary"], layout["wall_thickness_mm"] / 2
    xs = [-half, boundary["width"] + half]
    ys = [-half, boundary["height"] + half]
    for _, _, points, _ in paths:
        xs.extend(point[0] for point in points)
        ys.extend(point[1] for point in points)
    left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
    scale = min((_PLAN_BOUNDS[2] - _PLAN_BOUNDS[0]) / (right - left),
                (_PLAN_BOUNDS[3] - _PLAN_BOUNDS[1]) / (top - bottom))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Installation source projection is not finite and positive")
    projection = {"scale_pt_per_mm": scale,
                  "origin_pt": [_PLAN_BOUNDS[0] - left*scale, _PLAN_BOUNDS[1] - bottom*scale]}
    ox, oy = projection["origin_pt"]
    for _, _, points, closed in paths:
        painted = [(x*scale + ox, y*scale + oy) for x, y in points]
        if any(not math.isfinite(value) for point in painted for value in point) or len(set(painted)) != len(painted) or \
                any(math.dist(a, b) <= 1e-7 for a, b in zip(painted, painted[1:])):
            raise ValueError("Installation projection loses distinct source vertices")
        if closed and (max(x for x, _ in painted) - min(x for x, _ in painted) < 1 or
                       max(y for _, y in painted) - min(y for _, y in painted) < 1):
            raise ValueError("Installation source contour is unreadable at the admitted overview scale")
        if any(not _PLAN_BOUNDS[0] - .002 <= x <= _PLAN_BOUNDS[2] + .002 or
               not _PLAN_BOUNDS[1] - .002 <= y <= _PLAN_BOUNDS[3] + .002 for x, y in painted):
            raise ValueError("Installation projection exceeds its readable plan frame")
    return projection


def _contract(path_limits, *, declared=None):
    _valid_path_limits(path_limits)
    declared = path_limits if declared is None else declared
    _valid_path_limits(declared)

    def review_loader(source, *, limits):
        return load_process_review_with_paths(source, limits=limits, path_limits=path_limits)

    styles = {name: dict(style) for name, style in {**_document._STYLES, **_PATH_STYLES}.items()}
    return _document._DocumentContract(version="0.2", scope=_SCOPE, review_loader=review_loader,
        admit_review=_admit_review, sections=_sections, source_paths=_path_records, projection=_projection,
        styles=styles, extra_index={"path_limits": asdict(declared)}, check_appendix=_check_appendix,
        legend=_legend, legend_size=7.0, legend_leading=10.0, legend_wrap=True,
        plan_bounds=_PLAN_BOUNDS, plan_title="Literal process sources and supplied installation geometry")


def export_process_documentation_with_paths(source_path, destination, *, limits=ProcessDocumentationLimits(),
                                          path_limits=ProcessInstallationPathLimits()):
    """Publish a new closed version 0.2 five-file DRAFT after fresh artifact QA."""
    return _document._export(source_path, destination, limits, contract=_contract(path_limits),
                             publish_directory=_publish_directory)


def validate_process_documentation_with_paths(source_path, destination, *, limits=ProcessDocumentationLimits(),
                                            path_limits=ProcessInstallationPathLimits()):
    """Recompute exact source evidence and inspect actual saved text and paths."""
    report = _document._base_qa(_document._DocumentContract(version="0.2"))
    try:
        _document._valid_limits(limits)
        _valid_path_limits(path_limits)
        root = Path(destination)
        index_path = root / "index.json"
        if not root.is_dir() or root.is_symlink() or index_path.is_symlink() or \
                not index_path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Installation document metadata must stay within its actual directory")
        data = _document._read_bytes(index_path, limits.max_metadata_bytes)
        index = _document._read_json(data, limits.max_metadata_bytes, limits)
        declared = index["path_limits"]
        if type(declared) is not dict or set(declared) != set(asdict(ProcessInstallationPathLimits())):
            raise ValueError("Installation document declared path limits are not closed")
        declared = ProcessInstallationPathLimits(**declared)
        _valid_path_limits(declared)
        effective = ProcessInstallationPathLimits(**{name: min(value, getattr(declared, name))
            for name, value in asdict(path_limits).items()})
        result = _document._validate(source_path, destination, limits, contract=_contract(effective, declared=declared))
        if _document._read_bytes(index_path, limits.max_metadata_bytes) != data:
            raise ValueError("Installation document declared limits changed during fresh readback")
        return result
    except Exception as exc:
        report["issues"].append({"code": "PROCESS_DOCUMENT_READBACK", "status": "FAIL", "message": str(exc)[:1024]})
        return report
