"""Explicit unchanged-facility attachments for source-bound process/path DRAFTs.

Version 0.2 preserves the original facility child and adds no geometric
authority to it. Artifact consistency can pass alongside a known path FAIL;
complete installation and engineering acceptance remain open.
"""

from dataclasses import asdict, fields
import math
from pathlib import Path

from . import process_documentation as _document
from . import process_facility_package as _package
from . import process_installation_documentation as _path_document
from .orthogonal_bundle import _publish_directory
from .process_documentation import ProcessDocumentationLimits
from .process_facility_package import ProcessFacilityPackageLimits
from .process_installation_documentation import (export_process_documentation_with_paths,
                                                validate_process_documentation_with_paths)
from .process_installation_review import ProcessInstallationPathLimits


_SCOPE = "Unchanged single-variant facility delivery and synchronized supplied process/installation documents only"
_MANIFEST_KEYS = {"format", "version", "scope", "status", "ok", "review_status", "identity", "review_source", "base_kind",
    "base_profile_sha256", "base_projection_options", "files", "limits", "full_facility_validation", "full_process_validation",
    "engineering_acceptance", "authenticated_approval", "path_limits"}
_INDEX_KEYS = {"format", "version", "status", "ok", "review_status", "identity", "review_source", "files", "pages", "projection",
    "full_facility_validation", "engineering_acceptance", "authenticated_approval", "scope", "limits", "path_limits"}


def _limits(value, cls, admit):
    if type(value) is not dict or set(value) != set(asdict(cls())):
        raise ValueError("Declared attachment controls require a complete closed limit dictionary")
    result = cls(**value)
    admit(result)
    return result


def _minimum(cls, *values):
    return cls(**{row.name: min(getattr(value, row.name) for value in values) for row in fields(cls)})


def _digest(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _identity(value):
    if type(value) is not dict or set(value) != {"drawing_revision", "variant", "component_sha256"} or \
            type(value["drawing_revision"]) is not str or not 1 <= len(value["drawing_revision"]) <= 128 or \
            type(value["variant"]) is not int or value["variant"] < 1 or \
            type(value["component_sha256"]) is not dict or set(value["component_sha256"]) != set(_document._COMPONENTS) or \
            not all(_digest(digest) for digest in value["component_sha256"].values()):
        raise ValueError("Attachment metadata lost its exact common source identity")


def _source_identity(value):
    if type(value) is not dict or set(value) != {"file_sha256", "bytes", "scope"} or not _digest(value["file_sha256"]) or \
            type(value["bytes"]) is not int or value["bytes"] < 1 or type(value["scope"]) is not str or \
            not 1 <= len(value["scope"]) <= 1024:
        raise ValueError("Attachment metadata requires a bounded original whole-file identity")


def _scoped(value, scope):
    if value["version"] != "0.2" or value["scope"] != scope or value["status"] != "DRAFT" or value["ok"] is not False or \
            value["review_status"] not in ("UNKNOWN", "FAIL") or \
            not _package._same(value["full_facility_validation"], {"status": "UNKNOWN", "ok": False}) or \
            value["engineering_acceptance"] != "PENDING" or value["authenticated_approval"] != "UNKNOWN":
        raise ValueError("Explicit attachment metadata changed its version or unapproved scope")
    _identity(value["identity"])
    _source_identity(value["review_source"])


def _metadata(manifest, index):
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS or manifest["format"] != _package._FORMAT:
        raise ValueError("Version 0.2 outer manifest fields are not closed")
    if type(index) is not dict or set(index) != _INDEX_KEYS or index["format"] != _document._FORMAT:
        raise ValueError("Version 0.2 process index fields are not closed")
    _scoped(manifest, _SCOPE)
    _scoped(index, _path_document._SCOPE)
    if manifest["full_process_validation"] != "UNKNOWN" or not _digest(manifest["base_profile_sha256"]):
        raise ValueError("Outer metadata changed its original profile or full process scope")
    _package._options(manifest["base_projection_options"], manifest["base_kind"])
    if not all(_package._same(manifest[name], index[name]) for name in ("identity", "review_source", "review_status")):
        raise ValueError("Outer and process metadata do not describe the same original source")
    if type(manifest["files"]) is not dict:
        raise ValueError("Outer child inventory requires an actual dictionary")
    for name, row in manifest["files"].items():
        _package._relative(name)
        if not name.startswith(("facility/", "process/")) or type(row) is not dict or set(row) != {"sha256", "bytes"} or \
                not _digest(row["sha256"]) or type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError("Outer child references are escaped, malformed or unbounded")
    if type(index["files"]) is not dict or set(index["files"]) != set(_document._FILES):
        raise ValueError("Process child file references are not closed")
    for kind, name in _document._FILES.items():
        row = index["files"][kind]
        if type(row) is not dict or set(row) != {"file", "sha256"} or row["file"] != name or not _digest(row["sha256"]):
            raise ValueError("Process child file identity is invalid")
    if type(index["pages"]) is not list:
        raise ValueError("Process page inventory requires an actual list")
    kinds = {"identity", "schedule", "report", "plan", "installation_coverage", "installation_path"}
    for number, row in enumerate(index["pages"], 1):
        if type(row) is not dict or set(row) != {"page_id", "page_number", "kind", "source_id"} or \
                type(row["page_number"]) is not int or row["page_number"] != number or row["page_id"] != f"PR-{number:04d}" or \
                row["kind"] not in kinds or row["source_id"] is not None and \
                (type(row["source_id"]) is not str or not 1 <= len(row["source_id"]) <= 128):
            raise ValueError("Process page controls have missing, reordered or unsupported fields")
    projection = index["projection"]
    if type(projection) is not dict or set(projection) != {"scale_pt_per_mm", "origin_pt"} or \
            type(projection["scale_pt_per_mm"]) not in (int, float) or not math.isfinite(projection["scale_pt_per_mm"]) or \
            projection["scale_pt_per_mm"] <= 0 or type(projection["origin_pt"]) is not list or len(projection["origin_pt"]) != 2 or \
            any(type(value) not in (int, float) or not math.isfinite(value) for value in projection["origin_pt"]):
        raise ValueError("Process projection controls are not finite and closed")


def _child_limits(limits, *, base_bytes=0):
    controls = asdict(_package._document_limits(limits))
    remaining = limits.max_total_bytes - base_bytes
    if remaining <= 0:
        raise ValueError("No outer delivery budget remains for complete process documents")
    controls["max_total_bytes"] = min(controls["max_total_bytes"], remaining)
    for name in ("max_input_bytes", "max_report_bytes", "max_metadata_bytes"):
        controls[name] = min(controls[name], limits.max_file_bytes, limits.max_json_bytes, remaining)
    controls["max_pdf_bytes"] = min(controls["max_pdf_bytes"], limits.max_file_bytes, remaining)
    result = ProcessDocumentationLimits(**controls)
    _document._valid_limits(result)
    return result


def _active_contract(paths, declared_paths, document_limits, prepare, *, finish=None):
    _path_document._valid_path_limits(paths)

    def controls(_):
        return document_limits

    def exporter(source, destination, *, limits):
        # Resolve the public symbol at call time so actual input-change tests
        # can inspect the boundary after this child has really been saved.
        return export_process_documentation_with_paths(source, destination, limits=limits, path_limits=paths)

    def validator(source, destination, *, limits):
        return validate_process_documentation_with_paths(source, destination, limits=limits, path_limits=paths)

    return _package._PackageContract(version="0.2", scope=_SCOPE,
        document_contract=_path_document._contract(paths), document_limits=controls,
        export_documents=exporter, validate_documents=validator, extra_manifest={"path_limits": asdict(declared_paths)},
        prepare_validation=prepare, finish_validation=finish)


def _contract(path_limits):
    _path_document._valid_path_limits(path_limits)

    def prepare(source, original_base, package, limits, verify_saved):
        # This entire capture/admission precedes the first fresh source/model
        # reader. Neither child gets an independent reset of the outer budget.
        root = Path(package)
        expected = _package._ROOT_INVENTORY if verify_saved else _package._ROOT_INVENTORY - {"qa.json"}
        if not root.is_dir() or _package._linked(root) or {row.name for row in root.iterdir()} != expected:
            raise ValueError("Explicit outer package root inventory is not closed")
        captured = _package._snapshot(root, limits)
        parsed = _package._bounded_json(captured, limits)
        manifest, index = parsed.get("manifest.json"), parsed.get("process/index.json")
        _metadata(manifest, index)
        metadata = (captured["manifest.json"], captured["process/index.json"])
        outer = _limits(manifest["limits"], ProcessFacilityPackageLimits, _package._valid_limits)
        child = _limits(index["limits"], ProcessDocumentationLimits, _document._valid_limits)
        outer_paths = _limits(manifest["path_limits"], ProcessInstallationPathLimits, _path_document._valid_path_limits)
        child_paths = _limits(index["path_limits"], ProcessInstallationPathLimits, _path_document._valid_path_limits)
        effective = _minimum(ProcessFacilityPackageLimits, limits, outer)
        paths = _minimum(ProcessInstallationPathLimits, path_limits, outer_paths, child_paths)
        _package._valid_limits(effective)
        _path_document._valid_path_limits(paths)
        captured = _package._snapshot(root, effective)
        if captured.get("manifest.json") != metadata[0] or captured.get("process/index.json") != metadata[1]:
            raise ValueError("Pre-admitted outer or process controls changed during effective metadata recapture")
        parsed = _package._bounded_json(captured, effective)
        original = _package._snapshot(original_base, effective, maximum=effective.max_base_bytes)
        _package._bounded_json(original, effective)
        base = {name[9:]: data for name, data in captured.items() if name.startswith("facility/")}
        process = {name[8:]: data for name, data in captured.items() if name.startswith("process/")}
        if base != original or set(process) != _package._PROCESS_INVENTORY or \
                manifest["base_kind"] == "orthogonal" and set(base) != _package._ORTHOGONAL_INVENTORY:
            raise ValueError("An unchanged original facility and complete process child are required")
        document = _minimum(ProcessDocumentationLimits, _child_limits(effective, base_bytes=sum(map(len, base.values()))), child)
        _document._valid_limits(document)
        for name, data in (("manifest.json", captured["manifest.json"]), ("index.json", process["index.json"])):
            _document._read_json(data, document.max_metadata_bytes, document)
        source_bytes = _document._read_bytes(source, document.max_input_bytes)
        if process["source.json"] != source_bytes or index["review_source"]["bytes"] != len(source_bytes) or \
                index["review_source"]["file_sha256"] != _package._sha(source_bytes):
            raise ValueError("Declared child source differs from the exact original process bytes")
        if len(index["pages"]) > document.max_pages:
            raise ValueError("Process page inventory exceeds its effective admitted bound")
        for name, data in process.items():
            maximum = document.max_input_bytes if name == "source.json" else document.max_report_bytes if name == "review.json" else \
                document.max_pdf_bytes if name == "documentation.pdf" else document.max_metadata_bytes
            if len(data) > maximum:
                raise ValueError("Process child exceeds its effective file/source delivery bound")
            if name.endswith(".json"):
                _document._read_json(data, maximum, document)
        if sum(map(len, process.values())) > document.max_total_bytes:
            raise ValueError("Process child exceeds its effective total delivery bound")
        _package._artifact_bounds(_package._child_files(base, process), effective)
        artifact = ProcessFacilityPackageLimits(**{name: min(value, getattr(document, name)) if hasattr(document, name) else value
            for name, value in asdict(effective).items()})
        _package._artifact_bounds(process, artifact)
        def finish(directory):
            if _document._read_bytes(Path(directory) / "manifest.json", document.max_metadata_bytes) != metadata[0] or \
                    _document._read_bytes(Path(directory) / "process" / "index.json", document.max_metadata_bytes) != metadata[1]:
                raise ValueError("Pre-admitted outer or process controls changed during fresh physical readback")

        # The outer declaration and child declaration stay separate in their
        # saved files; the same min controls protect initial source and child QA.
        return effective, _active_contract(paths, outer_paths, document, prepare, finish=finish)

    def prepare_export(base, limits):
        _package._bounded_json(base, limits)
        _package._artifact_bounds(base, limits)
        document = _child_limits(limits, base_bytes=sum(map(len, base.values())))
        return _active_contract(path_limits, path_limits, document, prepare)

    return _package._PackageContract(version="0.2", scope=_SCOPE,
        extra_manifest={"path_limits": asdict(path_limits)}, prepare_validation=prepare, prepare_export=prepare_export)


def export_process_facility_package_with_paths(review_source, original_base, destination, *, base_kind="orthogonal",
        include_windows=False, include_grid=False, include_groups=False, limits=ProcessFacilityPackageLimits(),
        path_limits=ProcessInstallationPathLimits()):
    """Publish an original-bound version 0.2 attachment after fresh child QA."""
    return _package._export(review_source, original_base, destination, base_kind=base_kind,
        include_windows=include_windows, include_grid=include_grid, include_groups=include_groups, limits=limits,
        contract=_contract(path_limits), publish_directory=_publish_directory)


def validate_process_facility_package_with_paths(review_source, original_base, package, *, limits=ProcessFacilityPackageLimits(),
                                                path_limits=ProcessInstallationPathLimits()):
    """Admit all declared controls before inspecting fresh source/path geometry."""
    try:
        contract = _contract(path_limits)
    except Exception as exc:
        report = _package._base_report(_package._PackageContract(version="0.2", scope=_SCOPE))
        report["issues"].append({"code": "PROCESS_FACILITY_PACKAGE_READBACK", "status": "FAIL", "message": str(exc)[:1024]})
        return report
    return _package._validate(review_source, original_base, package, limits, contract=contract)
