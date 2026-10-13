"""Explicit CLI publication and independent QA of orthogonal draft sets.

The set index is delivery metadata over existing canonical source models.
Draft completion and exchange PASS never imply full facility acceptance.
"""

import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import sys
import tempfile

from .facility import DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, FacilityProfile, default_facility_profile
from .orthogonal_bundle import (FORMAT as BUNDLE_FORMAT, FULL_UNKNOWN, SOURCE_FILES, OrthogonalBundleLimits,
                               _grid_capability, _grid_options, _group_capability, _group_options, _hash, _json,
                               _keys, _publish_directory, _read_json, _source_frames, _source_grid, write_orthogonal_bundle)
from .orthogonal_bundle_qa import validate_orthogonal_bundle
from .orthogonal_generation import OrthogonalGenerationBudget, generate_orthogonal_facility_variants
from .orthogonal_validation import _window_options
from .orthogonal_window_capability import ORTHOGONAL_WINDOW_CAPABILITY, validate_orthogonal_window_capability
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping, normalize_mapping, validate_mapping


SET_FORMAT = "FLC-orthogonal-draft-set"
SET_LIMITS = OrthogonalBundleLimits(max_json_bytes=8_388_608, max_json_nodes=200000)
INPUT_LIMITS = OrthogonalBundleLimits()


def _bytes(path, maximum):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Input file is missing or exceeds its byte budget")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Input file exceeds its byte budget")
    return data


def _input(path):
    path = Path(path)
    data = _bytes(path, INPUT_LIMITS.max_json_bytes)
    if path.suffix.lower() == ".json":
        raw = _read_json(data, INPUT_LIMITS)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        class UniqueLoader(yaml.SafeLoader):
            pass

        def mapping(loader, node):
            values = {}
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise ValueError("Canonical YAML cannot merge mappings")
                key = loader.construct_object(key_node)
                if key in values:
                    raise ValueError("Duplicate YAML mapping key")
                values[key] = loader.construct_object(value_node)
            return values

        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        try:
            raw = yaml.load(data.decode("utf-8"), Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise ValueError("Malformed canonical YAML") from exc
        # The same finite/structure budget applies before any IR conversion.
        raw = _read_json(_json(raw), INPUT_LIMITS)
    else:
        raise ValueError("Canonical input requires JSON or YAML")
    if not isinstance(raw, dict):
        raise ValueError("Canonical input requires an object")
    return raw, data


def _profile(path):
    if path is None:
        return default_facility_profile(), None
    raw, data = _input(path)
    if validate_mapping(raw, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH):
        raise ValueError("Raw facility profile does not match its canonical schema")
    return FacilityProfile.from_mapping(raw), data


def _save(path, value):
    data = _json(value)
    _read_json(data, SET_LIMITS)
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _fresh(path):
    if path.exists() or path.is_symlink():
        raise FileExistsError("Orthogonal draft output must be a new directory")


def _publish_failure(target, failure):
    _fresh(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".orthogonal-set-", dir=target.parent) as temporary:
        stage = Path(temporary) / "failure"
        stage.mkdir()
        _save(stage / "generation-failure.json", failure)
        _fresh(target)
        _publish_directory(stage, target)


def _failure(stage, status, message, **evidence):
    return {"status": status, "ok": False, "stage": stage, "message": str(message)[:2000],
            "scope": "Explicit bounded orthogonal draft generation and delivery only",
            "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING",
            "proven_building_infeasible": False, **evidence}


def generate_draft_set(args):
    """Publish complete or verified partial drafts without touching prior output."""
    target = Path(args.output).absolute()
    stage_name, generation_evidence, delivery = "input", None, None
    input_evidence = {}
    try:
        include_windows = getattr(args, "include_windows", False)
        include_grid = getattr(args, "include_grid", False)
        include_groups = getattr(args, "include_groups", False)
        window_options = _window_options(include_windows)
        projection_options = {**window_options, **_grid_options(include_grid)}
        bundle_options = {**projection_options, **_group_options(include_groups)}
        _fresh(target)
        raw, source_bytes = _input(args.spec)
        input_evidence["source_file_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        # BuildingIR's schema leaves nested LayoutIR as an object. Validate raw
        # types here before constructors can coerce boolean/string dimensions.
        normalize_mapping(raw.get("layout"))
        if include_grid:
            # Validate raw axes before retaining the existing canonical float
            # normalization used by BuildingIR and the unchanged generator.
            _source_grid(raw)
        building = normalize_building_mapping(raw)
        _grid_capability(building, include_grid)
        profile, profile_bytes = _profile(args.profile)
        if profile_bytes is not None:
            input_evidence["profile_file_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
        budget = OrthogonalGenerationBudget(max_room_candidates=args.max_room_candidates,
                                            max_equipment_retries=args.equipment_retries)
        stage_name = "generation"
        generated = generate_orthogonal_facility_variants(
            building, profile=profile, variants=args.variants, seed=args.seed,
            time_limit_seconds=args.time_limit, deterministic_units=args.deterministic_units, budget=budget, **window_options)
        generation_evidence = generated.to_dict(building)
        if include_grid or include_groups:
            generation_evidence["input_evidence"] = dict(input_evidence)
        if not generated.accepted:
            _publish_failure(target, _failure("generation", "UNKNOWN", "No verified draft within configured search",
                                             generation=generation_evidence, input_evidence=input_evidence))
            print("UNKNOWN: no verified draft; full facility validation UNKNOWN; engineering acceptance PENDING")
            return 4
        stage_name = "export"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".orthogonal-set-", dir=target.parent) as temporary:
            stage = Path(temporary) / "set"
            stage.mkdir()
            generation_hash = _save(stage / "generation.json", generation_evidence)
            entries = []
            capabilities = []
            grid_capabilities = []
            group_capabilities = []
            for candidate in generated.accepted:
                variant = candidate.result.variant
                name = f"variant_{variant:02d}"
                write_orthogonal_bundle(stage / name, building, candidate.result, candidate.equipment,
                                        routing=candidate.flow_routes, profile=profile, **bundle_options)
                if include_windows:
                    capabilities.append({"variant": variant,
                        "report": validate_orthogonal_window_capability(building.layout, candidate.result).to_dict()})
                if include_grid:
                    grid_capabilities.append({"variant": variant, "report": _grid_capability(building, True)})
                if include_groups:
                    group_capabilities.append({"variant": variant,
                        "report": _group_capability(building, candidate.result, candidate.equipment, candidate.flow_routes, True)})
                entries.append({"variant": variant, "bundle": name,
                                "manifest_sha256": hashlib.sha256((stage / name / "manifest.json").read_bytes()).hexdigest()})
            index = {"format": SET_FORMAT, "version": "0.1", "status": generated.status, "ok": False,
                     "project_name": building.layout.project_name, "revision": profile.drawing.revision,
                     "source": generated.evidence["source"], "requested_variants": generated.requested_variants,
                     "published_variants": len(entries), "variants": entries,
                     "generation": {"file": "generation.json", "sha256": generation_hash},
                     "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING"}
            if include_windows:
                index["window_capability"] = {"policy": ORTHOGONAL_WINDOW_CAPABILITY, "variants": capabilities}
            if include_grid:
                index["grid_capability"] = {"policy": "boundary_spanning_axes_v1", "variants": grid_capabilities}
            if include_groups:
                index["group_capability"] = {"policy": "declared_semantic_groups_v1", "variants": group_capabilities}
            _save(stage / "manifest.json", index)
            delivery = validate_draft_input(stage, expected_profile=profile, **bundle_options)
            if delivery["status"] == "FAIL" or any(not row["report"]["ok"] for row in delivery["variants"]):
                raise ValueError("Fresh persisted draft-set delivery checks failed")
            if _bytes(args.spec, INPUT_LIMITS.max_json_bytes) != source_bytes or (
                    args.profile is not None and _bytes(args.profile, INPUT_LIMITS.max_json_bytes) != profile_bytes):
                raise ValueError("Canonical source/profile changed during draft generation")
            _fresh(target)
            _publish_directory(stage, target)
        print(f"{generated.status}: {len(entries)}/{generated.requested_variants} draft bundles in {target}; "
              "full facility validation UNKNOWN; engineering acceptance PENDING")
        return 0 if generated.status == "DRAFT" else 4
    except (ValueError, OSError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError) as exc:
        code = 2 if stage_name == "input" or generation_evidence is None else 3
        status = "INPUT_REJECTED" if code == 2 else "DELIVERY_REJECTED"
        failure = _failure(stage_name, status, exc, input_evidence=input_evidence)
        if generation_evidence is not None:
            failure["generation"] = generation_evidence
        if delivery is not None:
            failure["delivery"] = delivery
        try:
            _publish_failure(target, failure)
        except (OSError, ValueError):
            # Existing output is protected even when recording a new failure.
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return code


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Set source identity requires an actual SHA256")
    return value


def _check(report, identifier, status, evidence):
    report["checks"].append({"id": identifier, "status": status, "evidence": [str(evidence)[:2000]]})


def _finish(report):
    states = {row["status"] for row in report["checks"]}
    report["status"] = "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
    report["ok"] = report["status"] == "PASS"
    return report


def validate_draft_input(path, *, expected_profile=None, include_windows=False, include_grid=False, include_groups=False):
    """Check every child independently; a partial set remains UNKNOWN."""
    report = {"format": "FLC-orthogonal-draft-set-qa", "version": "0.1", "status": "FAIL", "ok": False,
              "scope": "Persisted exchange delivery, source-set consistency and declared software predicates only",
              "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING",
              "checks": [], "variants": []}
    path = Path(path).absolute()
    try:
        window_options = _window_options(include_windows)
        projection_options = {**window_options, **_grid_options(include_grid)}
        bundle_options = {**projection_options, **_group_options(include_groups)}
        if not path.is_dir() or path.is_symlink():
            raise ValueError("Draft input must be an actual directory without a symlink")
        index_path = path / "manifest.json"
        if index_path.is_symlink():
            raise ValueError("Draft manifest cannot be a symlink")
        index = _read_json(_bytes(index_path, SET_LIMITS.max_json_bytes), SET_LIMITS)
        if not isinstance(index, dict):
            raise ValueError("Draft manifest requires an object")
        if index.get("format") == BUNDLE_FORMAT:
            return validate_orthogonal_bundle(path, expected_profile=expected_profile, **bundle_options)
        _keys(index, ("format", "version", "status", "ok", "project_name", "revision", "source",
                      "requested_variants", "published_variants", "variants", "generation",
                      "full_facility_validation", "engineering_acceptance",
                      *(("window_capability",) if include_windows else ()), *(("grid_capability",) if include_grid else ()),
                      *(("group_capability",) if include_groups else ())))
        if (index["format"] != SET_FORMAT or index["version"] != "0.1" or index["ok"] is not False or
                _json(index["full_facility_validation"]) != _json(FULL_UNKNOWN) or index["engineering_acceptance"] != "PENDING"):
            raise ValueError("Set index cannot promote draft/full acceptance or change its format")
        if any(not isinstance(index[name], str) or not index[name].strip() for name in ("project_name", "revision")):
            raise ValueError("Draft set project/revision identities require nonempty text")
        for name in ("requested_variants", "published_variants"):
            if type(index[name]) is not int or not 1 <= index[name] <= 128:
                raise ValueError("Draft set counts must be integers in 1..128")
        requested, published = index["requested_variants"], index["published_variants"]
        if published > requested or index["status"] != ("DRAFT" if published == requested else "UNKNOWN"):
            raise ValueError("Draft set status/counts disagree")
        _keys(index["source"], ("building_sha256", "profile_sha256"))
        for value in index["source"].values():
            _digest(value)
        if expected_profile is not None and _hash(expected_profile.to_dict()) != index["source"]["profile_sha256"]:
            raise ValueError("Caller profile differs from draft-set source")
        if not isinstance(index["variants"], list) or len(index["variants"]) != published:
            raise ValueError("Draft set variant records disagree with actual published count")
        if include_windows:
            capability = index["window_capability"]
            _keys(capability, ("policy", "variants"))
            if capability["policy"] != ORTHOGONAL_WINDOW_CAPABILITY or not isinstance(capability["variants"], list) or len(capability["variants"]) != published:
                raise ValueError("Draft set window capability must identify every actual published variant")
            for position, row in enumerate(capability["variants"], 1):
                _keys(row, ("variant", "report"))
                if type(row["variant"]) is not int or row["variant"] != position:
                    raise ValueError("Window capability variant identities must agree with the ordered set")
        if include_grid:
            capability = index["grid_capability"]
            _keys(capability, ("policy", "variants"))
            if (capability["policy"] != "boundary_spanning_axes_v1" or not isinstance(capability["variants"], list)
                    or len(capability["variants"]) != published):
                raise ValueError("Draft set grid capability must identify every actual published variant")
            for position, row in enumerate(capability["variants"], 1):
                _keys(row, ("variant", "report"))
                if type(row["variant"]) is not int or row["variant"] != position:
                    raise ValueError("Grid capability variant identities must agree with the ordered set")
        if include_groups:
            capability = index["group_capability"]
            _keys(capability, ("policy", "variants"))
            if (capability["policy"] != "declared_semantic_groups_v1" or not isinstance(capability["variants"], list)
                    or len(capability["variants"]) != published):
                raise ValueError("Draft set group capability must identify every actual published variant")
            for position, row in enumerate(capability["variants"], 1):
                _keys(row, ("variant", "report"))
                if type(row["variant"]) is not int or row["variant"] != position:
                    raise ValueError("Group capability variant identities must agree with the ordered set")
        names = []
        for position, entry in enumerate(index["variants"], 1):
            _keys(entry, ("variant", "bundle", "manifest_sha256"))
            if type(entry["variant"]) is not int or entry["variant"] != position or entry["bundle"] != f"variant_{position:02d}":
                raise ValueError("Variant IDs/paths must be ordered unique canonical relative names")
            _digest(entry["manifest_sha256"])
            child = path / entry["bundle"]
            if child.is_symlink() or not child.is_dir() or child.resolve().parent != path.resolve():
                raise ValueError("Variant path cannot traverse or follow a symlink")
            names.append(entry["bundle"])
        inventory = tuple(islice(path.iterdir(), 131))
        if len(inventory) > 130 or {child.name for child in inventory} != {"manifest.json", "generation.json", *names}:
            raise ValueError("Draft set has missing or unexpected directory inventory")
        _keys(index["generation"], ("file", "sha256"))
        if index["generation"]["file"] != "generation.json" or (path / "generation.json").is_symlink():
            raise ValueError("Generation evidence requires its fixed actual filename")
        data = _bytes(path / "generation.json", SET_LIMITS.max_json_bytes)
        if hashlib.sha256(data).hexdigest() != _digest(index["generation"]["sha256"]):
            raise ValueError("Generation evidence actual byte hash differs")
        generation = _read_json(data, SET_LIMITS)
        if include_grid or include_groups:
            declared_input = generation.get("input_evidence")
            if (not isinstance(declared_input, dict) or "source_file_sha256" not in declared_input
                    or set(declared_input) - {"source_file_sha256", "profile_file_sha256"}):
                raise ValueError("Declared input provenance requires bounded source and optional profile SHA256 fields")
            for value in declared_input.values():
                _digest(value)
            report["input_evidence"] = dict(declared_input)
            report["input_evidence_scope"] = ("Declared input-file digests bound by generation bytes; "
                "external raw input cannot be reconstructed or independently verified from digests alone")
        if (generation["source"] != index["source"] or generation["status"] != index["status"] or
                any(type(generation[name]) is not int for name in ("requested_variants", "accepted_candidates")) or
                generation["requested_variants"] != requested or generation["accepted_candidates"] != published or
                generation["ok"] is not False or _json(generation["full_facility_validation"]) != _json(FULL_UNKNOWN) or
                generation["engineering_acceptance"] != "PENDING" or generation["proven_building_infeasible"] is not False or
                not isinstance(generation["accepted"], list) or len(generation["accepted"]) != published):
            raise ValueError("Generation evidence disagrees with actual draft-set source/counts/scope")
        _check(report, "SET_INDEX", "PASS", "Closed metadata, actual generation hash and ordered relative variant IDs agree")
        for entry, accepted in zip(index["variants"], generation["accepted"]):
            child = path / entry["bundle"]
            qa = validate_orthogonal_bundle(child, expected_profile=expected_profile, **bundle_options)
            report["variants"].append({"variant": entry["variant"], "bundle": entry["bundle"], "report": qa})
            _check(report, f"VARIANT_{entry['variant']:02d}_DELIVERY", qa["status"], "Fresh independent persisted child QA")
            try:
                child_manifest = child / "manifest.json"
                if child_manifest.is_symlink():
                    raise ValueError("Child manifest cannot be a symlink")
                child_data = _bytes(child_manifest, SET_LIMITS.max_json_bytes)
                raw = _read_json(child_data, SET_LIMITS)
                if hashlib.sha256(child_data).hexdigest() != entry["manifest_sha256"]:
                    raise ValueError("Child manifest actual bytes differ from set identity")
                if (raw["variant"] != entry["variant"] or raw["revision"] != index["revision"] or
                        raw["project_name"] != index["project_name"] or
                        any(raw["sources"][key]["canonical_sha256"] != index["source"][key + "_sha256"]
                            for key in ("building", "profile"))):
                    raise ValueError("Child canonical variant/source/profile/project/revision differs from set")
                for key, filename in (("layout", "layout.json"), ("equipment", "equipment.json"),
                                      ("routing", "routing.json"), ("facility_report", "facility-predicates.json")):
                    source = child / filename
                    if source.is_symlink() or _json(_read_json(_bytes(source, SET_LIMITS.max_json_bytes), SET_LIMITS)) != _json(accepted[key]):
                        raise ValueError("Accepted generation snapshot differs from actual child " + key)
                if include_windows:
                    source_building = _read_json(_bytes(child / "building.json", SET_LIMITS.max_json_bytes), SET_LIMITS)
                    normalize_mapping(source_building.get("layout"))
                    building = normalize_building_mapping(source_building)
                    layout = parse_layout_result(_read_json(_bytes(child / "layout.json", SET_LIMITS.max_json_bytes), SET_LIMITS))
                    fresh = validate_orthogonal_window_capability(building.layout, layout).to_dict()
                    claimed = index["window_capability"]["variants"][entry["variant"] - 1]["report"]
                    if (_json(claimed) != _json(fresh) or _json(raw["window_capability"]) != _json(fresh) or
                            _json(qa.get("window_capability")) != _json(fresh)):
                        raise ValueError("Draft set window capability differs from fresh actual canonical child source")
                if include_grid:
                    source_building = _read_json(_bytes(child / "building.json", SET_LIMITS.max_json_bytes), SET_LIMITS)
                    _source_grid(source_building)
                    building = normalize_building_mapping(source_building)
                    fresh = _grid_capability(building, True)
                    claimed = index["grid_capability"]["variants"][entry["variant"] - 1]["report"]
                    if (_json(claimed) != _json(fresh) or _json(raw["grid_capability"]) != _json(fresh)
                            or _json(qa.get("grid_capability")) != _json(fresh)):
                        raise ValueError("Draft set grid capability differs from fresh actual canonical child source")
                if include_groups:
                    frames = {}
                    for name, filename in SOURCE_FILES.items():
                        source = child / filename
                        if source.is_symlink():
                            raise ValueError("Group capability source cannot follow a symlink")
                        frames[name] = _read_json(_bytes(source, SET_LIMITS.max_json_bytes), SET_LIMITS)
                    building, layout, equipment, routing, _ = _source_frames(frames, **projection_options)
                    fresh = _group_capability(building, layout, equipment, routing, True)
                    claimed = index["group_capability"]["variants"][entry["variant"] - 1]["report"]
                    if (_json(claimed) != _json(fresh) or _json(raw["group_capability"]) != _json(fresh)
                            or _json(qa.get("group_capability")) != _json(fresh)):
                        raise ValueError("Draft set group capability differs from fresh actual canonical child source")
                _check(report, f"VARIANT_{entry['variant']:02d}_IDENTITY", "PASS", "Actual canonical source snapshots and revision agree")
            except (ValueError, OSError, TypeError, KeyError) as exc:
                _check(report, f"VARIANT_{entry['variant']:02d}_IDENTITY", "FAIL", exc)
        _check(report, "REQUESTED_COUNT", "PASS" if published == requested else "UNKNOWN",
               f"Published {published} of {requested} requested drafts; finite search never proves program infeasibility")
    except (ValueError, OSError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError) as exc:
        _check(report, "SET_INDEX", "FAIL", exc)
    return _finish(report)


def check_draft_command(args):
    try:
        profile = _profile(args.profile)[0] if args.profile is not None else None
        report = validate_draft_input(args.input, expected_profile=profile,
            **_window_options(getattr(args, "include_windows", False)), **_grid_options(getattr(args, "include_grid", False)),
            **_group_options(getattr(args, "include_groups", False)))
    except (ValueError, OSError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError) as exc:
        report = _failure("input", "FAIL", exc)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"Orthogonal draft delivery QA: {report['status']}; full facility validation UNKNOWN; engineering acceptance PENDING")
        for check in report.get("checks", []):
            print(f"{check['status']}: {check['id']}")
    return 0 if report["ok"] else 4
