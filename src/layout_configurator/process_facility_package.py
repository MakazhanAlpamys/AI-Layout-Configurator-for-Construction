"""An unchanged single-variant facility child and exact process-document child.

This opt-in outer package does not extend either existing child inventory.
PASS establishes artifact consistency against two independently supplied
original inputs. It never upgrades process, installation or engineering scope.
"""

from dataclasses import asdict, dataclass, fields
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from types import SimpleNamespace
from zipfile import ZipFile

from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject

from .engineering_library import _strict_json
from .facility import DEFAULT_FACILITY_PROFILE_SCHEMA_PATH
from .orthogonal_bundle import INVENTORY as _ORTHOGONAL_INVENTORY, OrthogonalBundleLimits, _publish_directory
from .orthogonal_bundle_qa import validate_orthogonal_bundle
from .orthogonal_pdf_qa import _decode, _resources
from .process_documentation import (ProcessDocumentationLimits, _current_review,
    _json, _read_bytes, _read_json, export_process_documentation, validate_process_documentation)
from .process_review import _nested_nodes
from .qa import validate_building_bundle
from .schema import validate_mapping


_FORMAT = "FLC-process-facility-package-draft"
_ROOT_INVENTORY = {"facility", "process", "manifest.json", "qa.json"}
_PROCESS_INVENTORY = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}
_SCOPE = "Unchanged single-variant facility delivery and synchronized process documents only"
_OPTION_KEYS = {"include_windows", "include_grid", "include_groups"}


@dataclass(frozen=True)
class _PackageContract:
    """Private immutable per-call choices for an explicit attachment version."""
    version: str = "0.1"
    scope: str = _SCOPE
    document_contract: object = None
    document_limits: object = None
    export_documents: object = None
    validate_documents: object = None
    extra_manifest: object = None
    prepare_validation: object = None
    prepare_export: object = None
    finish_validation: object = None


def _choice(contract, name, default):
    value = getattr(contract, name, None)
    return default if value is None else value


@dataclass(frozen=True)
class ProcessFacilityPackageLimits(ProcessDocumentationLimits):
    max_total_bytes: int = 67_108_864
    max_files: int = 128
    max_file_bytes: int = 16_777_216
    max_base_bytes: int = 33_554_432
    max_json_bytes: int = 8_388_608
    max_json_nodes: int = 262_144
    max_json_depth: int = 64
    max_directory_depth: int = 8
    max_zip_entries: int = 1024
    max_zip_expanded_bytes: int = 33_554_432
    max_ifc_entities: int = 20_000
    max_dxf_pairs: int = 200_000


def _valid_limits(limits):
    defaults = ProcessFacilityPackageLimits()
    if type(limits) is not ProcessFacilityPackageLimits or any(type(getattr(limits, row.name)) is not int or
            not 1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults)) or limits.max_report_bytes < 4096:
        raise ValueError("Outer package limits require positive bounded actual integers")


def _document_limits(limits):
    defaults = ProcessDocumentationLimits()
    values = {row.name: min(getattr(limits, row.name), getattr(defaults, row.name)) for row in fields(defaults)}
    # The exact input becomes a child file; reject impossible outer file/total
    # bounds before the process reader reaches source constructors or geometry.
    values["max_input_bytes"] = min(values["max_input_bytes"], limits.max_file_bytes, limits.max_total_bytes)
    return ProcessDocumentationLimits(**values)


def _selected_document_limits(limits, contract=None):
    return _choice(contract, "document_limits", _document_limits)(limits)


def _package_review(source, limits, contract=None):
    document = _selected_document_limits(limits, contract)
    selected = _choice(contract, "document_contract", None)
    return _current_review(source, document) if selected is None else _current_review(source, document, contract=selected)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _same(first, second):
    return _json(first) == _json(second)


def _linked(path):
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _relative(name):
    if type(name) is not str or not name or len(name) > 1024 or "\\" in name:
        raise ValueError("Package files require bounded relative POSIX paths")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or str(path) != name or ":" in name:
        raise ValueError("Package inventory has an escaped or noncanonical path")
    return path


def _snapshot(root, limits, *, maximum=None):
    root = Path(root)
    if not root.is_dir() or _linked(root):
        raise ValueError("Source and package children require actual bounded directories")
    root = root.resolve()
    queue, result, directories, total = [(root, 0)], {}, set(), 0
    while queue:
        parent, depth = queue.pop()
        if depth > limits.max_directory_depth:
            raise ValueError("Package directory depth budget exceeded")
        entries = 0
        for child in parent.iterdir():
            entries += 1
            if entries > limits.max_files + 2 or _linked(child) or not child.resolve().is_relative_to(root):
                raise ValueError("Package directory is oversized, linked or escaped")
            name = child.relative_to(root).as_posix()
            _relative(name)
            if child.is_dir():
                directories.add(name)
                if len(directories) > limits.max_files:
                    raise ValueError("Package directory count budget exceeded")
                queue.append((child, depth + 1))
            elif child.is_file():
                if len(result) >= limits.max_files:
                    raise ValueError("Package file count budget exceeded")
                result[name] = _read_bytes(child, limits.max_file_bytes)
                total += len(result[name])
                if total > (limits.max_total_bytes if maximum is None else min(maximum, limits.max_total_bytes)):
                    raise ValueError("Aggregate package byte budget exceeded")
            else:
                raise ValueError("Package inventory includes a nonregular filesystem entry")
    parents = {str(parent) for name in result for parent in PurePosixPath(name).parents if str(parent) != "."}
    if directories != parents:
        raise ValueError("Undeclared empty directories are outside the package contract")
    return result


def _bounded_json(snapshot, limits):
    """Charge all lexical JSON work before decoding any captured JSON file."""
    guard = SimpleNamespace(max_nodes=limits.max_json_nodes, max_depth=limits.max_json_depth)
    text, nodes = {}, 0
    for name, data in snapshot.items():
        if name.lower().endswith(".json"):
            if len(data) > limits.max_json_bytes:
                raise ValueError("Captured JSON file exceeds its byte bound")
            value = data.decode("utf-8")
            nodes += _nested_nodes(value, guard)
            if nodes > limits.max_json_nodes:
                raise ValueError("Aggregate captured JSON node budget exceeded before parsing")
            text[name] = value
    parsed = {}
    document = _document_limits(limits)
    for name, value in text.items():
        parsed[name] = _read_json(value.encode("utf-8"), limits.max_json_bytes, document)
    return parsed


def _artifact_bounds(snapshot, limits):
    """Bound legacy/exchange parser work before invoking existing child QA."""
    operations, pages = 0, 0
    for name, data in snapshot.items():
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted base PDFs are not independently reviewable")
            pages += len(reader.pages)
            if pages > limits.max_pages:
                raise ValueError("Aggregate captured PDF page budget exceeded")
            for page in reader.pages:
                _resources(page, limits)
                streams = page.get("/Contents")
                if streams is None:
                    raise ValueError("Captured PDF page has no content stream")
                if not isinstance(streams, ArrayObject):
                    streams = [streams]
                if len(streams) > 16:
                    raise ValueError("Captured PDF content stream count exceeded")
                decoded = b"\n".join(_decode(item.get_object(), limits) for item in streams)
                if len(decoded) > limits.max_stream_bytes:
                    raise ValueError("Captured PDF decoded page budget exceeded")
                stream = DecodedStreamObject()
                stream.set_data(decoded)
                count = len(ContentStream(stream, reader).operations)
                operations += count
                if count > limits.max_operations or operations > limits.max_total_pdf_operations:
                    raise ValueError("Aggregate captured PDF operation budget exceeded")
        elif suffix == ".bcf":
            with ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > limits.max_zip_entries or sum(row.file_size for row in entries) > limits.max_zip_expanded_bytes:
                    raise ValueError("BCF entry or expanded byte budget exceeded before readback")
                for entry in entries:
                    _relative(entry.filename.rstrip("/"))
                    if entry.flag_bits & 1:
                        raise ValueError("Encrypted BCF entries are unsupported")
        elif suffix == ".ifc" and len(re.findall(rb"#[0-9]+\s*=", data)) > limits.max_ifc_entities:
            raise ValueError("IFC entity budget exceeded before native readback")
        elif suffix == ".dxf" and data.count(b"\n") > 2*limits.max_dxf_pairs:
            raise ValueError("DXF pair budget exceeded before entity parsing")


def _options(options, kind):
    if type(options) is not dict or set(options) != _OPTION_KEYS or any(type(value) is not bool for value in options.values()):
        raise ValueError("Base projection controls require closed actual booleans")
    if kind not in ("orthogonal", "rectangular") or kind == "rectangular" and any(options.values()):
        raise ValueError("Unsupported base kind or projection control for this child format")
    return options


def _binding(review, parsed, kind, options):
    _options(options, kind)
    frame = review["source_frames"]
    manifest = parsed.get("manifest.json")
    if type(manifest) is not dict:
        raise ValueError("Original base has no admitted manifest")
    if kind == "orthogonal":
        if manifest.get("format") != "FLC-orthogonal-bundle-draft" or set(parsed) != {
                "building.json", "layout.json", "equipment.json", "routing.json", "profile.json",
                "dxf-index.json", "pdf-index.json", "ifc-index.json", "facility-predicates.json", "manifest.json", "qa.json"}:
            raise ValueError("Original orthogonal child does not have its unchanged closed source inventory")
        building, layout, equipment, routing, profile = (parsed[name + ".json"] for name in ("building", "layout", "equipment", "routing", "profile"))
        if type(equipment) is not dict or set(equipment) != {"equipment"} or routing is None:
            raise ValueError("Outer attachment requires explicit complete actual equipment and routing frames")
        equipment = equipment["equipment"]
        revision, variant, selected = manifest.get("revision"), manifest.get("variant"), "layout.json"
    else:
        variants = manifest.get("variants")
        if type(variants) is not list or len(variants) != 1 or type(variants[0]) is not dict:
            raise ValueError("Rectangular attachment currently requires one complete source variant")
        entry = variants[0]
        selected = entry.get("json")
        if type(selected) is not str or str(_relative(selected)) != f"building_{review['identity']['variant']:02d}.json":
            raise ValueError("Rectangular child source name differs from the selected original variant")
        payload = parsed.get(selected)
        if type(payload) is not dict or any(key not in payload for key in ("spec", "layout", "equipment", "flow_routes", "facility_validation")):
            raise ValueError("Rectangular child lacks complete original saved source frames")
        building, layout, equipment, routing = (payload[key] for key in ("spec", "layout", "equipment", "flow_routes"))
        profile = payload["facility_validation"].get("profile") if type(payload["facility_validation"]) is dict else None
        if not _same(profile, manifest.get("facility_profile")):
            raise ValueError("Original rectangular source and manifest profiles differ")
        revision = profile.get("drawing", {}).get("revision") if type(profile) is dict else None
        variant = entry.get("variant")
        for key in ("dxf", "pdf", "ifc", "bcf", "coordination_issues"):
            if type(entry.get(key)) is not str:
                raise ValueError("Rectangular child is missing a mandatory artifact reference")
            _relative(entry[key])
    if not all(_same(actual, frame[name]) for name, actual in (("building", building), ("layout", layout),
            ("equipment", equipment), ("flow_routes", routing))):
        raise ValueError("Base and process sources differ in original whole frames, scalar types, clearance or actual routes")
    if type(variant) is not int or variant != review["identity"]["variant"] or type(layout.get("variant")) is not int or \
            layout["variant"] != variant or revision != review["identity"]["drawing_revision"]:
        raise ValueError("Base source drawing revision or actual variant differs from the process document")
    if type(profile) is not dict or validate_mapping(profile, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH) or \
            profile.get("drawing", {}).get("revision") != revision:
        raise ValueError("Base has no exact valid source profile/revision for cross-binding")
    return {"source": selected, "profile_sha256": _sha(_json(profile))}


def _base_qa(root, kind, review, options, limits):
    if kind == "orthogonal":
        defaults = OrthogonalBundleLimits()
        bounded = OrthogonalBundleLimits(max_json_bytes=min(defaults.max_json_bytes, limits.max_json_bytes),
            max_artifact_bytes=min(defaults.max_artifact_bytes, limits.max_file_bytes),
            max_total_bytes=min(defaults.max_total_bytes, limits.max_base_bytes, limits.max_total_bytes),
            max_dxf_entities=defaults.max_dxf_entities, max_json_nodes=min(defaults.max_json_nodes, limits.max_json_nodes),
            max_json_depth=min(defaults.max_json_depth, limits.max_json_depth))
        return validate_orthogonal_bundle(root, limits=bounded, **options)
    source = Path(root) / f"building_{review['identity']['variant']:02d}.json"
    result = validate_building_bundle(source, manifest_path=Path(root) / "manifest.json").to_dict()
    # The original reader's input path changes upon staging/rename. Preserve
    # all actual checks and make this location metadata explicitly relative.
    result["input"] = source.name
    result["input_path_policy"] = "relative_to_unchanged_facility_child"
    result["status"] = "PASS" if result["ok"] else "FAIL"
    return result


def _inventory(snapshot):
    return {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in sorted(snapshot.items())}


def _child_files(base, process):
    return {**{"facility/" + name: data for name, data in base.items()},
            **{"process/" + name: data for name, data in process.items()}}


def _base_report(contract=None):
    return {"format": "FLC-process-facility-package-qa", "version": _choice(contract, "version", "0.1"), "scope": _choice(contract, "scope", _SCOPE),
        "status": "FAIL", "ok": False, "document_status": "DRAFT", "review_status": "UNKNOWN", "issues": [],
        "base_qa": {"status": "UNKNOWN", "ok": False, "checks": []},
        "process_qa": {"status": "UNKNOWN", "ok": False, "issues": []},
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "full_process_validation": "UNKNOWN",
        "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN", "proven_building_infeasible": False}


def _validate(source, original_base, package, limits, *, verify_saved=True, contract=None):
    report = _base_report(contract)
    try:
        _valid_limits(limits)
        prepare = _choice(contract, "prepare_validation", None)
        if prepare is not None:
            limits, contract = prepare(source, original_base, package, limits, verify_saved)
            _valid_limits(limits)
        original, review = _package_review(source, limits, contract)
        report["review_status"] = review["status"]
        original_files = _snapshot(original_base, limits, maximum=limits.max_base_bytes)
        root = Path(package)
        if not root.is_dir() or _linked(root):
            raise ValueError("Outer package requires an actual directory")
        expected_root = _ROOT_INVENTORY if verify_saved else _ROOT_INVENTORY - {"qa.json"}
        if {path.name for path in root.iterdir()} != expected_root:
            raise ValueError("Outer package has missing or undeclared root entries")
        captured = _snapshot(root, limits)
        parsed = _bounded_json(captured, limits)
        manifest = parsed.get("manifest.json")
        keys = {"format", "version", "scope", "status", "ok", "review_status", "identity", "review_source", "base_kind",
            "base_profile_sha256", "base_projection_options", "files", "limits", "full_facility_validation",
            "full_process_validation", "engineering_acceptance", "authenticated_approval"}
        extras = _choice(contract, "extra_manifest", {})
        keys.update(extras)
        if type(manifest) is not dict or set(manifest) != keys or type(manifest["limits"]) is not dict or set(manifest["limits"]) != set(asdict(limits)):
            raise ValueError("Outer manifest or declared limits are not closed")
        declared = ProcessFacilityPackageLimits(**manifest["limits"])
        _valid_limits(declared)
        effective = ProcessFacilityPackageLimits(**{name: min(value, getattr(declared, name)) for name, value in asdict(limits).items()})
        _valid_limits(effective)
        if not _same(asdict(effective), asdict(limits)):
            original, review = _package_review(source, effective, contract)
            original_files = _snapshot(original_base, effective, maximum=effective.max_base_bytes)
            captured = _snapshot(root, effective)
            parsed = _bounded_json(captured, effective)
        kind, options = manifest["base_kind"], manifest["base_projection_options"]
        _options(options, kind)
        base = {name[9:]: data for name, data in captured.items() if name.startswith("facility/")}
        process = {name[8:]: data for name, data in captured.items() if name.startswith("process/")}
        if base != original_files or set(process) != _PROCESS_INVENTORY or process["source.json"] != original:
            raise ValueError("An unchanged original base or exact process source child is missing")
        if kind == "orthogonal" and set(base) != _ORTHOGONAL_INVENTORY:
            raise ValueError("Original orthogonal child inventory must remain exactly fourteen files")
        original_parsed = _bounded_json(original_files, effective)
        binding = _binding(review, original_parsed, kind, options)
        expected = {"format": _FORMAT, "version": _choice(contract, "version", "0.1"), "scope": _choice(contract, "scope", _SCOPE), "status": "DRAFT", "ok": False,
            "review_status": review["status"], "identity": review["identity"], "review_source": review["review_source"],
            "base_profile_sha256": binding["profile_sha256"], "files": _inventory(_child_files(base, process)),
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "full_process_validation": "UNKNOWN",
            "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN", **extras}
        if not _same({key: manifest[key] for key in expected}, expected):
            raise ValueError("Outer source, profile, revision, child bytes or acceptance scope changed")
        for name in manifest["files"]:
            _relative(name)
        _artifact_bounds(_child_files(base, process), effective)
        report["base_qa"] = _base_qa(root / "facility", kind, review, options, effective)
        if not report["base_qa"]["ok"]:
            raise ValueError("Fresh original-format physical facility QA failed")
        report["process_qa"] = _choice(contract, "validate_documents", validate_process_documentation)(
            source, root / "process", limits=_selected_document_limits(effective, contract))
        if not report["process_qa"]["ok"]:
            raise ValueError("Fresh current-source process document readback failed")
        if _read_bytes(source, effective.max_input_bytes) != original or _snapshot(original_base, effective, maximum=effective.max_base_bytes) != original_files or \
                _snapshot(root, effective) != captured:
            raise ValueError("Original source, original base or copied artifacts changed during readback")
        finish = _choice(contract, "finish_validation", None)
        if finish is not None:
            finish(root)
        report.update(status="PASS", ok=True, identity=review["identity"], review_source=review["review_source"],
            manifest_sha256=_sha(captured["manifest.json"]))
        if verify_saved and not _same(parsed["qa.json"], report):
            raise ValueError("Saved outer QA differs from fresh source and physical child checks")
    except Exception as exc:
        report.update(status="FAIL", ok=False)
        report["issues"].append({"status": "FAIL", "code": "PROCESS_FACILITY_PACKAGE_READBACK", "message": str(exc)[:1024]})
    return report


def validate_process_facility_package(review_source, original_base, package, *, limits=ProcessFacilityPackageLimits()):
    """Check both original inputs, exact copied children and fresh physical QA."""
    return _validate(review_source, original_base, package, limits)


def _export(review_source, original_base, destination, *, base_kind="orthogonal",
        include_windows=False, include_grid=False, include_groups=False, limits=ProcessFacilityPackageLimits(),
        contract=None, publish_directory=None):
    """Publish a new outer DRAFT without changing either original child format."""
    _valid_limits(limits)
    options = _options({"include_windows": include_windows, "include_grid": include_grid, "include_groups": include_groups}, base_kind)
    source, base, target = Path(review_source), Path(original_base), Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("Outer package destination already exists")
    if target.resolve().is_relative_to(base.resolve()):
        raise ValueError("Outer destination cannot modify the original base directory")
    prepare = _choice(contract, "prepare_export", None)
    if prepare is None:
        original, review = _package_review(source, limits, contract)
        base_files = _snapshot(base, limits, maximum=limits.max_base_bytes)
    else:
        base_files = _snapshot(base, limits, maximum=limits.max_base_bytes)
        contract = prepare(base_files, limits)
        original, review = _package_review(source, limits, contract)
    if base_kind == "orthogonal" and set(base_files) != _ORTHOGONAL_INVENTORY:
        raise ValueError("Original orthogonal child requires its unchanged fourteen-file inventory")
    if len(base_files) + 7 > limits.max_files:
        raise ValueError("Complete outer child/file inventory exceeds its count bound")
    parsed = _bounded_json(base_files, limits)
    binding = _binding(review, parsed, base_kind, options)
    _artifact_bounds(base_files, limits)
    base_qa = _base_qa(base, base_kind, review, options, limits)
    if not base_qa["ok"]:
        raise ValueError("Original base failed fresh existing-format acceptance QA")
    if _snapshot(base, limits, maximum=limits.max_base_bytes) != base_files or _read_bytes(source, limits.max_input_bytes) != original:
        raise ValueError("Original source or accepted base changed before copying")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".process-facility-package-", dir=target.parent) as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        facility = stage / "facility"
        facility.mkdir()
        for name, data in base_files.items():
            path = facility / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        _choice(contract, "export_documents", export_process_documentation)(
            source, stage / "process", limits=_selected_document_limits(limits, contract))
        if _read_bytes(source, limits.max_input_bytes) != original or _snapshot(base, limits, maximum=limits.max_base_bytes) != base_files:
            raise ValueError("Original source or base changed while saving process documents")
        process = _snapshot(stage / "process", limits)
        children = _child_files(base_files, process)
        manifest = {"format": _FORMAT, "version": _choice(contract, "version", "0.1"), "scope": _choice(contract, "scope", _SCOPE), "status": "DRAFT", "ok": False,
            "review_status": review["status"], "identity": review["identity"], "review_source": review["review_source"],
            "base_kind": base_kind, "base_profile_sha256": binding["profile_sha256"], "base_projection_options": options,
            "files": _inventory(children), "limits": asdict(limits), "full_facility_validation": {"status": "UNKNOWN", "ok": False},
            "full_process_validation": "UNKNOWN", "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
            **_choice(contract, "extra_manifest", {})}
        manifest_bytes = _json(manifest)
        if len(manifest_bytes) > limits.max_metadata_bytes:
            raise ValueError("Outer manifest exceeds its bounded metadata delivery")
        (stage / "manifest.json").write_bytes(manifest_bytes)
        qa = _validate(source, base, stage, limits, verify_saved=False, contract=contract)
        if not qa["ok"]:
            raise ValueError("Staged outer package failed fresh independent QA: " + qa["issues"][0]["message"])
        qa_bytes = _json(qa)
        if len(qa_bytes) > limits.max_metadata_bytes:
            raise ValueError("Outer QA exceeds its bounded metadata delivery")
        (stage / "qa.json").write_bytes(qa_bytes)
        captured = _snapshot(stage, limits)
        if _snapshot(base, limits, maximum=limits.max_base_bytes) != base_files or _read_bytes(source, limits.max_input_bytes) != original:
            raise ValueError("Original source or base changed before native publication")
        # Saving QA changes only the declared final metadata entry; physical
        # children remain byte-identical to the independently checked stage.
        if any(captured[name] != data for name, data in children.items()) or captured["manifest.json"] != manifest_bytes:
            raise ValueError("Copied child or manifest changed after staged physical QA")
        (_publish_directory if publish_directory is None else publish_directory)(stage, target)
    final = _validate(source, base, target, limits, contract=contract)
    if not final["ok"]:
        raise ValueError("Published outer DRAFT failed current-source verification: " + final["issues"][0]["message"])
    return _strict_json(manifest_bytes.decode("utf-8"))


def export_process_facility_package(review_source, original_base, destination, *, base_kind="orthogonal",
        include_windows=False, include_grid=False, include_groups=False, limits=ProcessFacilityPackageLimits()):
    """Publish the original version 0.1 attachment with unchanged child formats."""
    return _export(review_source, original_base, destination, base_kind=base_kind, include_windows=include_windows,
        include_grid=include_grid, include_groups=include_groups, limits=limits)
