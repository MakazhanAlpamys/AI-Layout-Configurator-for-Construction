"""Unchanged facility bytes with explicit production-requirement documents.

The version 0.3 outer attachment preserves both original inputs and the
existing facility child formats. Artifact consistency never establishes full
process, installation or engineering acceptance.
"""

from dataclasses import asdict, replace
import io
import math
from pathlib import Path

from pypdf import PdfReader

from . import process_documentation as _document
from . import process_facility_package as _package
from . import process_installation_facility_package as _path_package
from . import process_installation_review as _paths
from . import process_production_layout_documentation as _production_document
from . import process_review as _common
from .orthogonal_bundle import _publish_directory
from .process_documentation import ProcessDocumentationLimits
from .process_facility_package import ProcessFacilityPackageLimits
from .process_installation_review import ProcessInstallationPathLimits
from .process_production_layout_documentation import (
    export_process_documentation_with_production_layout,
    validate_process_documentation_with_production_layout)
from .production_layout_review import ProductionLayoutLimits


_SCOPE = "Unchanged single-variant facility delivery and synchronized supplied production requirement documents only"
_MANIFEST_KEYS = {"format", "version", "scope", "status", "ok", "review_status", "identity", "review_source", "base_kind",
    "base_profile_sha256", "base_projection_options", "files", "limits", "full_facility_validation", "full_process_validation",
    "engineering_acceptance", "authenticated_approval", "path_limits", "production_limits", "include_windows"}


def _scoped(value, scope):
    if (value["version"] != "0.3" or value["scope"] != scope or value["status"] != "DRAFT" or value["ok"] is not False or
            value["review_status"] not in ("UNKNOWN", "FAIL") or
            not _package._same(value["full_facility_validation"], {"status": "UNKNOWN", "ok": False}) or
            value["engineering_acceptance"] != "PENDING" or value["authenticated_approval"] != "UNKNOWN"):
        raise ValueError("Production attachment metadata changed its version or unapproved scope")
    _path_package._identity(value["identity"])
    _path_package._source_identity(value["review_source"])


def _metadata(manifest, index):
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS or manifest["format"] != _package._FORMAT:
        raise ValueError("Version 0.3 outer manifest fields are not closed")
    if (type(index) is not dict or set(index) != _production_document._INDEX_KEYS or
            index["format"] != _document._FORMAT):
        raise ValueError("Version 0.3 production document index fields are not closed")
    _scoped(manifest, _SCOPE)
    _scoped(index, _production_document._SCOPE)
    if manifest["full_process_validation"] != "UNKNOWN" or not _path_package._digest(manifest["base_profile_sha256"]):
        raise ValueError("Outer metadata changed its original profile or full process scope")
    options = _package._options(manifest["base_projection_options"], manifest["base_kind"])
    if (type(manifest["include_windows"]) is not bool or type(index["include_windows"]) is not bool or
            manifest["include_windows"] is not index["include_windows"] or
            manifest["include_windows"] is not options["include_windows"]):
        raise ValueError("Outer, child and base window policies require the same actual boolean")
    if not all(_package._same(manifest[name], index[name]) for name in ("identity", "review_source", "review_status")):
        raise ValueError("Outer and production metadata do not describe the same original revision")
    if type(manifest["files"]) is not dict:
        raise ValueError("Outer child inventory requires an actual dictionary")
    for name, row in manifest["files"].items():
        _package._relative(name)
        if (not name.startswith(("facility/", "process/")) or type(row) is not dict or set(row) != {"sha256", "bytes"} or
                not _path_package._digest(row["sha256"]) or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise ValueError("Outer child references are escaped, malformed or unbounded")
    if type(index["files"]) is not dict or set(index["files"]) != set(_document._FILES):
        raise ValueError("Production child file references are not closed")
    for kind, name in _document._FILES.items():
        row = index["files"][kind]
        if (type(row) is not dict or set(row) != {"file", "sha256"} or row["file"] != name or
                not _path_package._digest(row["sha256"])):
            raise ValueError("Production child file identity is invalid")
    if type(index["pages"]) is not list:
        raise ValueError("Production page inventory requires an actual list")
    kinds = {"identity", "schedule", "report", "plan", "installation_coverage", "installation_path",
        "production_layout", "production_requirement"}
    for number, row in enumerate(index["pages"], 1):
        if (type(row) is not dict or set(row) != {"page_id", "page_number", "kind", "source_id"} or
                type(row["page_number"]) is not int or row["page_number"] != number or row["page_id"] != f"PR-{number:04d}" or
                row["kind"] not in kinds or row["source_id"] is not None and
                (type(row["source_id"]) is not str or not 1 <= len(row["source_id"]) <= 128)):
            raise ValueError("Production page controls have missing, reordered or unsupported fields")
    projection = index["projection"]
    if (type(projection) is not dict or set(projection) != {"scale_pt_per_mm", "origin_pt"} or
            type(projection["scale_pt_per_mm"]) not in (int, float) or not math.isfinite(projection["scale_pt_per_mm"]) or
            projection["scale_pt_per_mm"] <= 0 or type(projection["origin_pt"]) is not list or len(projection["origin_pt"]) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in projection["origin_pt"])):
        raise ValueError("Production projection controls are not finite and closed")


def _base_policy(parsed, kind, options):
    if kind != "orthogonal":
        return
    manifest = parsed["manifest.json"]
    scope = manifest.get("projection_scope")
    if type(scope) is not dict or type(scope.get("geometry_components")) is not list:
        raise ValueError("Original orthogonal base has no literal projection policy")
    components = scope["geometry_components"]
    for flag, capability, component in (("include_windows", "window_capability", "source_facade_windows"),
            ("include_grid", "grid_capability", "source_reference_axes")):
        if (capability in manifest) != options[flag] or (component in components) != options[flag]:
            raise ValueError("Original base capability differs from its exact outer projection policy")
    if ("group_capability" in manifest) != options["include_groups"]:
        raise ValueError("Original base group capability differs from its exact outer projection policy")


def _raw_review(raw, document):
    """Admit literal original frames for pure base binding, without models."""
    error = next(_paths._validator().iter_errors(raw), None)
    if error is not None:
        raise ValueError("Production attachment requires the unchanged closed source version 0.2: " + error.message[:512])
    _common._check_common_schema(raw)
    parent = _common.ProcessReviewLimits(**{name: getattr(document, name) for name in asdict(_common.ProcessReviewLimits())})
    _common._raw_preflight(raw, parent)
    return {"identity": _common._identity(raw), "source_frames": {name: raw[name] for name in _document._COMPONENTS}}


def _artifact_bounds(snapshot, limits):
    # Admit decoded streams, resources, per-page and combined operation work
    # before extracting any text. Text includes the actual line breaks emitted
    # by the PDF parser, with one shared counter for both persisted children.
    for name, data in snapshot.items():
        if name.endswith(".pdf") and len(data) > limits.max_pdf_bytes:
            raise ValueError("Captured PDF exceeds its outer byte bound")
    _package._artifact_bounds(snapshot, limits)
    characters = 0
    for name, data in snapshot.items():
        if name.endswith(".pdf"):
            reader = PdfReader(io.BytesIO(data), strict=True)
            for page in reader.pages:
                characters += len(page.extract_text())
                if characters > limits.max_text_chars:
                    raise ValueError("Aggregate actual facility and process PDF text exceeds its outer bound before geometry")


def _active_contract(paths, production, outer_paths, outer_production, document, windows, prepare,
                     *, child_document=None, child_paths=None, child_production=None, before_read=None, finish=None):
    child_document = document if child_document is None else child_document
    child_paths = paths if child_paths is None else child_paths
    child_production = production if child_production is None else child_production

    def controls(_):
        return document

    def exporter(source, destination, *, limits):
        if before_read is not None:
            before_read()
        return export_process_documentation_with_production_layout(source, destination, include_windows=windows,
            limits=limits, path_limits=paths, production_limits=production)

    def validator(source, destination, *, limits):
        if before_read is not None:
            before_read()
        return validate_process_documentation_with_production_layout(source, destination,
            limits=limits, path_limits=paths, production_limits=production)

    document_contract = _production_document._contract(paths, production, windows,
        declared_common=child_document, declared_path=child_paths, declared_production=child_production)
    if before_read is not None:
        admitted_loader = document_contract.review_loader

        def review_loader(source, *, limits):
            before_read()
            return admitted_loader(source, limits=limits)

        document_contract = replace(document_contract, review_loader=review_loader)

    return _package._PackageContract(version="0.3", scope=_SCOPE,
        document_contract=document_contract,
        document_limits=controls, export_documents=exporter, validate_documents=validator,
        extra_manifest={"path_limits": asdict(outer_paths), "production_limits": asdict(outer_production),
            "include_windows": windows}, prepare_validation=prepare, finish_validation=finish)


def _contract(path_limits, production_limits, *, source=None, original_base=None, base_kind="orthogonal", options=None):
    _production_document._paths._valid_path_limits(path_limits)
    _production_document._valid_production_limits(production_limits)

    def prepare(review_source, original_base, package, limits, verify_saved):
        root = Path(package)
        expected = _package._ROOT_INVENTORY if verify_saved else _package._ROOT_INVENTORY - {"qa.json"}
        if not root.is_dir() or _package._linked(root) or {row.name for row in root.iterdir()} != expected:
            raise ValueError("Production outer package root inventory is not closed")
        captured = _package._snapshot(root, limits)
        parsed = _package._bounded_json(captured, limits)
        manifest, index = parsed.get("manifest.json"), parsed.get("process/index.json")
        _metadata(manifest, index)
        metadata = (captured["manifest.json"], captured["process/index.json"])
        outer = _path_package._limits(manifest["limits"], ProcessFacilityPackageLimits, _package._valid_limits)
        child = _path_package._limits(index["limits"], ProcessDocumentationLimits, _document._valid_limits)
        outer_paths = _path_package._limits(manifest["path_limits"], ProcessInstallationPathLimits,
            _production_document._paths._valid_path_limits)
        child_paths = _path_package._limits(index["path_limits"], ProcessInstallationPathLimits,
            _production_document._paths._valid_path_limits)
        outer_production = _path_package._limits(manifest["production_limits"], ProductionLayoutLimits,
            _production_document._valid_production_limits)
        child_production = _path_package._limits(index["production_limits"], ProductionLayoutLimits,
            _production_document._valid_production_limits)
        effective = _path_package._minimum(ProcessFacilityPackageLimits, limits, outer)
        paths = _path_package._minimum(ProcessInstallationPathLimits, path_limits, outer_paths, child_paths)
        production = _path_package._minimum(ProductionLayoutLimits, production_limits, outer_production, child_production)
        _package._valid_limits(effective)
        _production_document._paths._valid_path_limits(paths)
        _production_document._valid_production_limits(production)
        captured = _package._snapshot(root, effective)
        if captured.get("manifest.json") != metadata[0] or captured.get("process/index.json") != metadata[1]:
            raise ValueError("Pinned initial outer or child controls changed during effective recapture")
        parsed = _package._bounded_json(captured, effective)
        original = _package._snapshot(original_base, effective, maximum=effective.max_base_bytes)
        original_parsed = _package._bounded_json(original, effective)
        base = {name[9:]: data for name, data in captured.items() if name.startswith("facility/")}
        process = {name[8:]: data for name, data in captured.items() if name.startswith("process/")}
        if (base != original or set(process) != _package._PROCESS_INVENTORY or
                manifest["base_kind"] == "orthogonal" and set(base) != _package._ORTHOGONAL_INVENTORY):
            raise ValueError("An unchanged original facility and complete production child are required")
        children = _package._child_files(base, process)
        if not _package._same(manifest["files"], _package._inventory(children)):
            raise ValueError("Actual child bytes differ from the pinned outer inventory")
        document = _path_package._minimum(ProcessDocumentationLimits,
            _path_package._child_limits(effective, base_bytes=sum(map(len, base.values()))), child)
        _document._valid_limits(document)
        _document._read_json(metadata[0], document.max_metadata_bytes, document)
        _document._read_json(metadata[1], document.max_metadata_bytes, document)
        if sum(map(len, process.values())) > document.max_total_bytes:
            raise ValueError("Production child exceeds its effective total byte bound")
        for name, data in process.items():
            maximum = (document.max_input_bytes if name == "source.json" else document.max_report_bytes
                if name == "review.json" else document.max_pdf_bytes if name == "documentation.pdf" else document.max_metadata_bytes)
            if len(data) > maximum:
                raise ValueError("Production child exceeds its effective source or artifact byte bound")
        process_parsed = _production_document._json_bounds(process, document)
        _artifact_bounds(children, effective)
        _production_document._pdf_bounds(process["documentation.pdf"], index, document)
        source_bytes = _document._read_bytes(review_source, document.max_input_bytes)
        if (process["source.json"] != source_bytes or index["review_source"]["bytes"] != len(source_bytes) or
                index["review_source"]["file_sha256"] != _package._sha(source_bytes)):
            raise ValueError("Production child differs from the exact current original process bytes")
        raw_review = _raw_review(process_parsed["source.json"], document)
        if not _package._same(raw_review["identity"], manifest["identity"]):
            raise ValueError("Pinned common identity differs from the original raw source frames")
        _base_policy(original_parsed, manifest["base_kind"], manifest["base_projection_options"])
        binding = _package._binding(raw_review, original_parsed, manifest["base_kind"], manifest["base_projection_options"])
        if binding["profile_sha256"] != manifest["base_profile_sha256"]:
            raise ValueError("Pinned original facility profile differs before source geometry")

        def pinned_inputs(directory=root):
            if (_document._read_bytes(Path(directory) / "manifest.json", document.max_metadata_bytes) != metadata[0] or
                    _document._read_bytes(Path(directory) / "process" / "index.json", document.max_metadata_bytes) != metadata[1]):
                raise ValueError("Pinned initial outer or child metadata changed during fresh physical readback")
            if (_package._snapshot(directory, effective) != captured or
                    _package._snapshot(original_base, effective, maximum=effective.max_base_bytes) != original or
                    _document._read_bytes(review_source, document.max_input_bytes) != source_bytes):
                raise ValueError("Original inputs or the initially captured child inventory changed during physical readback")

        return effective, _active_contract(paths, production, outer_paths, outer_production, document,
            manifest["include_windows"], prepare, child_document=child, child_paths=child_paths,
            child_production=child_production, before_read=pinned_inputs, finish=pinned_inputs)

    def prepare_export(base, limits):
        if source is None or original_base is None or options is None:
            raise ValueError("Production export requires its explicit original source and base projection policy")
        if len(base) + 7 > limits.max_files:
            raise ValueError("Complete production attachment file count exceeds its bound before source models")
        document = _path_package._child_limits(limits, base_bytes=sum(map(len, base.values())))
        source_bytes = _document._read_bytes(source, document.max_input_bytes)
        sentinel = "__original_process_source__.json"
        if sentinel in base:
            raise ValueError("Original base inventory collides with private source admission")
        combined = _package._bounded_json({**base, sentinel: source_bytes}, limits)
        raw_review = _raw_review(combined[sentinel], document)
        original_parsed = {name: combined[name] for name in base if name.endswith(".json")}
        _artifact_bounds(base, limits)
        _base_policy(original_parsed, base_kind, options)
        _package._binding(raw_review, original_parsed, base_kind, options)

        def pinned_inputs():
            if (_document._read_bytes(source, document.max_input_bytes) != source_bytes or
                    _package._snapshot(original_base, limits, maximum=limits.max_base_bytes) != base):
                raise ValueError("Initially admitted export source or original facility bytes changed before model reading")

        return _active_contract(path_limits, production_limits, path_limits, production_limits, document,
            options["include_windows"], prepare, before_read=pinned_inputs)

    return _package._PackageContract(version="0.3", scope=_SCOPE,
        extra_manifest={"path_limits": asdict(path_limits), "production_limits": asdict(production_limits)},
        prepare_validation=prepare, prepare_export=prepare_export)


def export_process_facility_package_with_production_layout(review_source, original_base, destination, *,
        base_kind="orthogonal", include_windows=False, include_grid=False, include_groups=False,
        limits=ProcessFacilityPackageLimits(), path_limits=ProcessInstallationPathLimits(), production_limits=ProductionLayoutLimits()):
    """Publish an original-bound 0.3 DRAFT after fresh native and child QA."""
    options = _package._options({"include_windows": include_windows, "include_grid": include_grid,
        "include_groups": include_groups}, base_kind)
    return _package._export(review_source, original_base, destination, base_kind=base_kind,
        include_windows=include_windows, include_grid=include_grid, include_groups=include_groups, limits=limits,
        contract=_contract(path_limits, production_limits, source=review_source, original_base=original_base,
            base_kind=base_kind, options=options),
        publish_directory=_publish_directory)


def validate_process_facility_package_with_production_layout(review_source, original_base, package, *,
        limits=ProcessFacilityPackageLimits(), path_limits=ProcessInstallationPathLimits(), production_limits=ProductionLayoutLimits()):
    """Admit pinned shared controls before fresh original-source geometry."""
    try:
        contract = _contract(path_limits, production_limits)
    except Exception as exc:
        report = _package._base_report(_package._PackageContract(version="0.3", scope=_SCOPE))
        report["issues"].append({"code": "PROCESS_FACILITY_PACKAGE_READBACK", "status": "FAIL", "message": str(exc)[:1024]})
        return report
    return _package._validate(review_source, original_base, package, limits, contract=contract)
