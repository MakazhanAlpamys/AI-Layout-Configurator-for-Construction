"""Explicit 0.3 DRAFT documents for one original 0.2 process source.

Production requirement pages retain original declarations and freshly measured
room contacts. Saved metadata controls work before fresh source geometry; it
never supplies a verdict. Existing process/path document formats stay intact.
"""

from dataclasses import asdict, replace
import io
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject

from . import process_documentation as _document
from . import process_installation_documentation as _paths
from . import process_installation_review as _path_review
from . import process_review as _common
from . import production_layout_review as _production
from .orthogonal_bundle import _publish_directory
from .process_documentation import ProcessDocumentationLimits
from .process_installation_review import ProcessInstallationPathLimits
from .process_production_layout_review import load_process_review_with_production_layout
from .production_layout_review import ProductionLayoutLimits


_SCOPE = "Synchronized supplied process, admitted installation geometry and actual production room requirements"
_INDEX_KEYS = {"format", "version", "status", "ok", "review_status", "identity", "review_source", "files",
    "pages", "projection", "full_facility_validation", "engineering_acceptance", "authenticated_approval",
    "scope", "limits", "path_limits", "production_limits", "include_windows"}


def _valid_production_limits(limits):
    if not _production._valid_limits(limits):
        raise ValueError("Production document limits require bounded actual integers and a supported delivery bound")


def _admit_review(review):
    _paths._admit_review(review)
    additional = review.get("additional_reports")
    if type(additional) is not dict or set(additional) != {"production_layout"}:
        raise ValueError("Production documents require a complete fresh additional report")
    wrapper = additional["production_layout"]
    if type(wrapper) is not dict or not _document._same(wrapper.get("identity"), review["identity"]):
        raise ValueError("Production evidence lost the original common component identity")
    evidence = wrapper.get("evidence")
    if (type(evidence) is not dict or wrapper.get("status") != evidence.get("status") or
            evidence.get("inventory_status") != "COMPLETE" or evidence.get("geometry_status") == "NOT_EVALUATED" or
            evidence.get("finding_inventory", {}).get("status") == "INCOMPLETE"):
        raise ValueError("Production requirement evidence is incomplete, capped or unevaluated")
    frame = review["source_frames"]
    base = {key: value for key, value in frame["building"].items() if key != "production_program"}
    source = {name + "_sha256": review["identity"]["component_sha256"][name]
        for name in ("building", "layout", "production_program")}
    source["program_base_building_sha256"] = _document._sha(_document._json(base))
    if not _document._same(evidence.get("source"), source) or not _document._same(
            evidence.get("source_frames"), {name: frame[name] for name in ("building", "layout", "production_program")}):
        raise ValueError("Production raw frames or whole/base source identities differ from the original process")
    program = frame["production_program"]
    originals, rows = program["requirements"], evidence.get("requirements")
    if type(rows) is not list or len(rows) != len(originals) or len({row["id"] for row in originals}) != len(originals):
        raise ValueError("Every original production requirement needs one complete fresh record")
    sources = {row["id"]: row for row in program["sources"]}
    fields = ("operation_id", "source_id", "kind", "room_a", "room_b", "reason", "excerpt", "basis")
    witness_fields = {"status", "shared_segments_mm", "maximum_contiguous_mm", "required_contiguous_mm", "overlap_area_mm2"}
    for original, row in zip(originals, rows):
        source_record = sources[original["source_id"]]
        if (type(row) is not dict or row.get("requirement_id") != original["id"] or
                row.get("requirement_sha256") != _document._sha(_document._json(original)) or
                not _document._same({name: row.get(name) for name in fields}, {name: original[name] for name in fields}) or
                row.get("source_sha256") != source_record["sha256"] or
                row.get("usage_rights_state") != source_record["usage_rights"]["state"] or
                not witness_fields <= set(row)):
            raise ValueError("Fresh production requirement identity, provenance or actual witness differs from its original declaration")


def _sections(review, include_windows):
    sections = _paths._sections(review)
    evidence = review["additional_reports"]["production_layout"]["evidence"]
    summary = {key: value for key, value in evidence.items() if key not in {"source_frames", "requirements"}}
    lines = ["Production report status: " + evidence["status"],
        "Production binding status: " + evidence["binding_status"],
        "Production provenance status: " + evidence["provenance_status"],
        "Production geometry status: " + evidence["geometry_status"],
        f"Include windows: {include_windows}",
        "Original production source identities: " + _document._json(evidence["source"]).decode("utf-8"),
        "Fresh scoped production evidence: " + _document._json(summary).decode("utf-8"),
        "Actual shared room spans do not establish transfer permission, capacity, complete installation or engineering acceptance.",
        "Full facility validation: UNKNOWN | Engineering acceptance: PENDING | Authenticated approval: UNKNOWN"]
    sections.append(("production_layout", None, "Fresh actual production room requirements", lines))
    program = review["source_frames"]["production_program"]
    sources = {row["id"]: row for row in program["sources"]}
    for original, row in zip(program["requirements"], evidence["requirements"]):
        source = sources[original["source_id"]]
        lines = ["Requirement ID: " + original["id"], "Requirement SHA256: " + row["requirement_sha256"],
            "Requirement status: " + row["status"], "Operation ID: " + original["operation_id"],
            "Source ID: " + original["source_id"], "Source SHA256: " + row["source_sha256"],
            "Source usage rights: " + _document._json(source["usage_rights"]).decode("utf-8"),
            "Original requirement: " + _document._json(original).decode("utf-8"),
            "Fresh actual requirement witness: " + _document._json(row).decode("utf-8"),
            "Basis and rights remain supplied declarations; this local result is not engineering approval."]
        sections.append(("production_requirement", original["id"], "Original production requirement: " + original["id"], lines))
    return sections


def _check_appendix(review, body, include_windows):
    _paths._check_appendix(review, body)
    for kind, identifier, _, lines in _sections(review, include_windows):
        if kind in {"production_layout", "production_requirement"} and not _document._inventory_words_match(
                "\n".join(body[(kind, identifier)]), lines):
            raise ValueError("Printed production identity, declaration, rights, local verdict or measured witness differs from fresh evidence")


def _contract(path_limits, production_limits, include_windows, *, declared_common=None, declared_path=None,
              declared_production=None, delivery_validator=None):
    _paths._valid_path_limits(path_limits)
    _valid_production_limits(production_limits)
    if type(include_windows) is not bool:
        raise ValueError("Production document window choice requires an actual boolean")
    declared_path = path_limits if declared_path is None else declared_path
    declared_production = production_limits if declared_production is None else declared_production
    _paths._valid_path_limits(declared_path)
    _valid_production_limits(declared_production)
    if declared_common is not None:
        _document._valid_limits(declared_common)

    def review_loader(source, *, limits):
        review = load_process_review_with_production_layout(source, include_windows=include_windows,
            limits=limits, path_limits=path_limits, production_limits=production_limits)
        if (review.get("inventory_status") in {"INCOMPLETE", "NOT_EVALUATED"} or review.get("identity") is None or
                set(review["source_frames"]) != set(_document._COMPONENTS) or
                set(review["schedules"]) != set(_document._SCHEDULES) or set(review["reports"]) != set(_document._REPORTS)):
            raise ValueError("Production documents require complete freshly admitted process evidence")
        _admit_review(review)
        # Runtime admission above uses the effective minima exactly once.
        # Only these authored policy annotations describe the pinned document
        # controls; source facts, geometry, findings and work counters stay fresh.
        authored = limits if declared_common is None else _common.ProcessReviewLimits(**{
            name: getattr(declared_common, name) for name in asdict(limits)})
        scenario = review["reports"]["equipment_scenarios"]["evidence"]
        if "limits" in scenario:
            scenario["limits"] = asdict(_common._scenario_limits(authored))
        leaf_limits = _path_review._leaf_limits(authored, declared_path)
        section = review["installation_paths"]
        for wrapper in section["reports"]:
            if "limits" in wrapper["evidence"]:
                wrapper["evidence"]["limits"] = asdict(leaf_limits)
        annotations = {"shared_source_geometry_vertex_limit": authored.max_scenario_vertices,
            "per_request_source_geometry_vertex_limit": leaf_limits.max_vertices,
            "shared_process_geometry_limit": authored.max_geometry_checks}
        for name, value in annotations.items():
            if name in section["workload"]:
                section["workload"][name] = value
        return review

    return replace(_paths._contract(path_limits, declared=declared_path), version="0.3", scope=_SCOPE,
        review_loader=review_loader, admit_review=_admit_review,
        sections=lambda review: _sections(review, include_windows),
        check_appendix=lambda review, body: _check_appendix(review, body, include_windows),
        extra_index={"path_limits": asdict(declared_path), "production_limits": asdict(declared_production),
            "include_windows": include_windows}, delivery_validator=delivery_validator)


def _root(destination, verify_saved):
    root = Path(destination)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Production documents require an actual directory")
    root = root.resolve()
    inventory = _document._INVENTORY if verify_saved else _document._INVENTORY - {"qa.json"}
    if {child.name for child in root.iterdir()} != inventory:
        raise ValueError("Production document inventory has missing or undeclared files")
    for name in inventory:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("Production document artifacts must be regular files inside their actual directory")
    return root, inventory


def _capture(root, limits, verify_saved):
    root, inventory = _root(root, verify_saved)
    snapshot, total = {}, 0
    for name in sorted(inventory):
        maximum = (limits.max_pdf_bytes if name == "documentation.pdf" else limits.max_input_bytes
            if name == "source.json" else limits.max_report_bytes if name == "review.json" else limits.max_metadata_bytes)
        snapshot[name] = _document._read_bytes(root / name, maximum)
        total += len(snapshot[name])
        if total > limits.max_total_bytes:
            raise ValueError("Combined captured document bytes exceed their effective bound")
    return snapshot


def _controls(index, limits, path_limits, production_limits, expected_windows):
    if (type(index) is not dict or set(index) != _INDEX_KEYS or index["format"] != _document._FORMAT or
            index["version"] != "0.3" or index["status"] != "DRAFT" or index["ok"] is not False or
            index["scope"] != _SCOPE or type(index["include_windows"]) is not bool):
        raise ValueError("Production document index requires its closed version 0.3 fields and actual window choice")
    if expected_windows is not None and index["include_windows"] is not expected_windows:
        raise ValueError("Saved window choice differs from the opted producer's actual boolean")
    if (index["full_facility_validation"] != {"status": "UNKNOWN", "ok": False} or
            index["engineering_acceptance"] != "PENDING" or index["authenticated_approval"] != "UNKNOWN"):
        raise ValueError("Production document metadata changed its DRAFT acceptance scope")
    declared, effective = [], []
    for name, caller, kind, validator in (("limits", limits, ProcessDocumentationLimits, _document._valid_limits),
            ("path_limits", path_limits, ProcessInstallationPathLimits, _paths._valid_path_limits),
            ("production_limits", production_limits, ProductionLayoutLimits, _valid_production_limits)):
        values = index[name]
        if type(values) is not dict or set(values) != set(asdict(caller)):
            raise ValueError("Production document declared " + name + " are not closed")
        saved = kind(**values)
        validator(saved)
        active = kind(**{field: min(value, getattr(saved, field)) for field, value in asdict(caller).items()})
        validator(active)
        declared.append(saved)
        effective.append(active)
    return declared, effective


def _json_bounds(snapshot, limits):
    guard = SimpleNamespace(max_nodes=limits.max_delivery_nodes, max_depth=limits.max_delivery_depth)
    nodes = 0
    for name, data in snapshot.items():
        if name.endswith(".json"):
            nodes += _common._nested_nodes(data.decode("utf-8"), guard)
            if nodes > limits.max_delivery_nodes:
                raise ValueError("Aggregate captured JSON nodes exceed their bound before fresh source geometry")
    parsed = {}
    for name, data in snapshot.items():
        if name.endswith(".json"):
            maximum = (limits.max_input_bytes if name == "source.json" else limits.max_report_bytes
                if name == "review.json" else limits.max_metadata_bytes)
            parsed[name] = _document._read_json(data, maximum, limits)
    return parsed


def _pdf_bounds(data, index, limits):
    """Reserve actual persisted PDF work without constructing source geometry."""
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= limits.max_pages:
        raise ValueError("Captured production PDF is unreadable or exceeds its page bound")
    if type(index["pages"]) is not list or len(index["pages"]) != len(reader.pages):
        raise ValueError("Captured PDF page count differs from its pinned index")
    operations, text_chars = 0, 0
    for page in reader.pages:
        _document._resources(page, limits)
        streams = page.get("/Contents")
        if streams is None:
            raise ValueError("Captured production PDF page has no content stream")
        if not isinstance(streams, ArrayObject):
            streams = [streams]
        if len(streams) > 16:
            raise ValueError("Captured production PDF content stream count exceeds its bound")
        decoded = b"\n".join(_document._decode(item.get_object(), limits) for item in streams)
        if len(decoded) > limits.max_stream_bytes:
            raise ValueError("Captured production PDF combined page stream exceeds its bound")
        stream = DecodedStreamObject()
        stream.set_data(decoded)
        count = len(ContentStream(stream, reader).operations)
        operations += count
        if count > limits.max_operations or operations > limits.max_total_pdf_operations:
            raise ValueError("Captured production PDF operation work exceeds its effective bound")
        text_chars += len(page.extract_text())
        if text_chars > limits.max_text_chars:
            raise ValueError("Captured production PDF text exceeds its effective bound")


def _validate(source, destination, limits, path_limits, production_limits, *, verify_saved=True, expected_windows=None):
    report = _document._base_qa(_document._DocumentContract(version="0.3"))
    try:
        _document._valid_limits(limits)
        _paths._valid_path_limits(path_limits)
        _valid_production_limits(production_limits)
        root, _ = _root(destination, verify_saved)
        initial_index = _document._read_bytes(root / "index.json", limits.max_metadata_bytes)
        index = _document._read_json(initial_index, limits.max_metadata_bytes, limits)
        declared, effective = _controls(index, limits, path_limits, production_limits, expected_windows)
        active_limits, active_path, active_production = effective
        snapshot = _capture(root, active_limits, verify_saved)
        if snapshot["index.json"] != initial_index:
            raise ValueError("Pinned initial document index changed before effective recapture")
        parsed = _json_bounds(snapshot, active_limits)
        if not _document._same(parsed["index.json"], index):
            raise ValueError("Pinned document controls changed during effective admission")
        _pdf_bounds(snapshot["documentation.pdf"], index, active_limits)
        original = _document._read_bytes(source, active_limits.max_input_bytes)
        if original != snapshot["source.json"]:
            raise ValueError("Captured document source differs from the exact current original")
        contract = _contract(active_path, active_production, index["include_windows"],
            declared_common=declared[0], declared_path=declared[1], declared_production=declared[2])
        result = _document._validate(source, root, active_limits, verify_saved=verify_saved, contract=contract)
        if not result["ok"]:
            return result
        if (_capture(root, active_limits, verify_saved) != snapshot or
                _document._read_bytes(source, active_limits.max_input_bytes) != original):
            raise ValueError("Original source or the pinned document inventory changed during fresh readback")
        return result
    except Exception as exc:
        report["issues"].append({"code": "PROCESS_DOCUMENT_READBACK", "status": "FAIL", "message": str(exc)[:1024]})
        return report


def export_process_documentation_with_production_layout(source_path, destination, *, include_windows=False,
        limits=ProcessDocumentationLimits(), path_limits=ProcessInstallationPathLimits(), production_limits=ProductionLayoutLimits()):
    """Publish a new five-file DRAFT with complete original requirement pages."""
    def validate_delivery(source, target, controls, *, verify_saved=True):
        return _validate(source, target, controls, path_limits, production_limits,
            verify_saved=verify_saved, expected_windows=include_windows)

    return _document._export(source_path, destination, limits,
        contract=_contract(path_limits, production_limits, include_windows, delivery_validator=validate_delivery),
        publish_directory=_publish_directory)


def validate_process_documentation_with_production_layout(source_path, destination, *,
        limits=ProcessDocumentationLimits(), path_limits=ProcessInstallationPathLimits(), production_limits=ProductionLayoutLimits()):
    """Read pinned saved controls, then inspect fresh source and actual PDF evidence."""
    return _validate(source_path, destination, limits, path_limits, production_limits)
