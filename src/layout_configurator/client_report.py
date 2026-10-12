"""Paginated client evidence from one exact canonical result revision.

The PDF explains saved measurements and check records. It does not perform
additional engineering calculations or authenticate a review/signature.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .compare import variant_metrics
from .drawing_sheets import _check_glyphs, _fonts, _wrap
from .facility import DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, FacilityProfile
from .io import load_building_result
from .schema import DEFAULT_BUILDING_SCHEMA_PATH, DEFAULT_SCHEMA_PATH, validate_mapping


DISCLAIMER = (
    "This report is not a permit, a GMP or code-compliance verdict, or an engineering approval. "
    "Process, cleanroom/HVAC and architectural reviews remain pending. Declared pressures are "
    "not ventilation calculations or evidence of actual air-system operation."
)
_STATUSES = {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_TEXT = 1_000_000
_MAX_PAGES = 256
_WIDTH, _HEIGHT = A4
_MARGIN = 48
_BODY_TOP, _BODY_BOTTOM = _HEIGHT - 122, 100


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate client source field: {key}")
        result[key] = value
    return result


def _source(path):
    data = path.read_bytes()
    if len(data) > _MAX_SOURCE_BYTES:
        raise ValueError("Client report source exceeds the 8 MiB bound")
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite source JSON")))
        json.dumps(payload, allow_nan=False).encode("utf-8")
    except (UnicodeError, TypeError, OverflowError) as exc:
        raise ValueError(f"Client source requires finite UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("spec"), dict):
        raise ValueError("Client report requires a canonical building result")
    spec = payload["spec"]
    for raw, schema in ((spec, DEFAULT_BUILDING_SCHEMA_PATH),
                        (spec.get("layout"), DEFAULT_SCHEMA_PATH)):
        issues = validate_mapping(raw, schema)
        if issues:
            raise ValueError(f"Invalid client result source: {issues[0].path}: {issues[0].message}")
    building, result, _ = load_building_result(path)
    report = payload.get("facility_validation")
    if not isinstance(report, dict) or type(report.get("ok")) is not bool:
        raise ValueError("Client report requires explicit saved facility validation")
    profile_raw = report.get("profile")
    errors = validate_mapping(profile_raw, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)
    if errors:
        raise ValueError(f"Invalid saved report profile: {errors[0].message}")
    profile = FacilityProfile.from_mapping(profile_raw)
    checks, issues = report.get("checks"), report.get("issues")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 1000:
        raise ValueError("Client report requires 1–1000 saved checks")
    if not isinstance(issues, list) or len(issues) > 1000:
        raise ValueError("Client report requires at most 1000 saved issues")
    check_ids, issue_ids = set(), set()
    rules = {rule.id: rule.to_dict() for rule in profile.rules}
    for check in checks:
        if (not isinstance(check, dict) or set(check) - {"id", "title", "status", "evidence", "rule"}
                or not _text(check.get("id")) or check["id"] in check_ids
                or not _text(check.get("title")) or not isinstance(check.get("status"), str)
                or check["status"] not in _STATUSES
                or not isinstance(check.get("evidence"), list) or not check["evidence"]
                or any(not _text(item) for item in check["evidence"])):
            raise ValueError("Malformed or duplicate saved client check")
        check_ids.add(check["id"])
        if "rule" in check:
            declared = check["rule"]
            if not isinstance(declared, dict) or not isinstance(declared.get("id"), str):
                raise ValueError("Saved check rule provenance must be explicit")
            rule = rules.get(declared["id"])
            if rule is None or declared != {key: rule[key] for key in ("id", "source", "edition", "effective_date", "severity")}:
                raise ValueError("Saved check rule provenance differs from the declared profile")
    if report["ok"] != all(check["status"] in {"PASS", "NOT_APPLICABLE"} for check in checks):
        raise ValueError("Saved facility acceptance contradicts its check statuses")
    for issue in issues:
        if (not isinstance(issue, dict) or not _text(issue.get("issue_id")) or issue["issue_id"] in issue_ids
                or not isinstance(issue.get("status"), str) or issue["status"] not in {"OPEN", "RESOLVED"}
                or any(not _text(issue.get(key)) for key in ("code", "title", "message", "severity", "source"))):
            raise ValueError("Malformed or duplicate saved coordination issue")
        issue_ids.add(issue["issue_id"])
    return payload, building, result, profile, hashlib.sha256(data).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _rule_lines(rule):
    return [f"Declared rule: {rule['id']}", f"Rule kind: {rule['kind']}", f"Rule title: {rule['title']}",
            f"Rule source: {rule['source']}", f"Edition: {rule['edition']}",
            f"Effective date: {rule['effective_date']}", f"Severity: {rule['severity']}",
            "Declared source evidence:", *rule["evidence"],
            "Declared parameters:", json.dumps(rule["parameters"], sort_keys=True, ensure_ascii=False, allow_nan=False)]


def write_client_report(source: str | Path, *, font_path: str | Path | None = None) -> dict:
    """Write a bounded vector PDF beside a canonical result and return its index."""
    source = Path(source)
    payload, building, result, profile, digest = _source(source)
    metrics = variant_metrics(source)
    report = payload["facility_validation"]
    rules = {rule["id"]: rule for rule in report["profile"]["rules"]}
    identity = {"json": source.name, "sha256": digest, "variant": result.variant,
                "project_name": building.layout.project_name, "revision": profile.drawing.revision,
                "profile_name": profile.name, "profile_version": profile.version}
    provenance = json.dumps(report["profile"]["provenance"], sort_keys=True, ensure_ascii=False)
    sections = [
        ("identity", None, None, "Source identity and acceptance scope", [
            f"Project: {identity['project_name']}", f"Variant {result.variant}",
            f"Drawing revision: {profile.drawing.revision}", f"Canonical source: {source.name}",
            f"Canonical source SHA256: {digest}", f"Profile: {profile.name} / {profile.version}",
            f"Domain: {profile.domain} | Jurisdiction: {profile.jurisdiction}",
            "Declared profile provenance:", provenance, "", "Engineering acceptance: PENDING", DISCLAIMER,
            "Saved check outcomes explain the selected candidate; they are not external sign-off."]),
        ("metrics", None, None, "Measured geometry and saved gate evidence", [
            f"Rooms: {metrics.rooms}", f"Total area deviation: {metrics.area_deviation_pct:.2f} %",
            f"Worst room deviation: {metrics.max_room_deviation_pct:.2f} % ({metrics.max_room_deviation_room or 'none'})",
            f"Total derived route length: {metrics.route_length_m:.1f} m",
            f"Derived door crossings: {metrics.door_crossings}",
            f"Boundary footprint ratio: {metrics.footprint_ratio:.3f}",
            *[f"Route type {kind}: {length:.1f} m" for kind, length in sorted(metrics.route_length_by_type_m.items())],
            f"Saved facility gate outcome: {metrics.gate_status}",
            f"Failed checks: {len(metrics.failed_checks)}", f"Unknown checks: {len(metrics.unknown_checks)}",
            f"Open coordination issues: {metrics.open_issues}", "",
            "Metric limits: route lengths follow saved polyline geometry. Door crossings count saved room-path transfers.",
            "Area deviation uses programmed targets. Footprint ratio uses rectangular room extents and the rectangular boundary.",
            "These measurements do not prove clean/dirty separation, evacuation compliance, serviceability or optimality."]),
    ]
    referenced = set()
    for check in report["checks"]:
        lines = [f"Check {check['id']} | Status: {check['status']}", f"Title: {check['title']}", "Saved evidence:", *check["evidence"]]
        if "rule" in check:
            rule_id = check["rule"]["id"]
            referenced.add(rule_id)
            lines += ["", *_rule_lines(rules[rule_id])]
        else:
            lines += ["", "No declared regulatory rule reference; this is saved software/project evidence."]
        if check["status"] == "UNKNOWN":
            lines += ["UNKNOWN remains unresolved. Additional source data or review is required."]
        sections.append(("check", check["id"], check["status"], "Facility check evidence", lines))
    for rule_id, rule in rules.items():
        if rule_id not in referenced:
            sections.append(("rule", rule_id, None, "Additional declared profile rule", [
                "Declared policy only; no saved check is linked to this rule.", *_rule_lines(rule)]))
    issue_lines = [f"Open coordination issues: {metrics.open_issues}",
                   "Saved issues include their recorded state; resolution is not engineering approval."]
    if not report["issues"]:
        issue_lines.append("No coordination issues are recorded in this source revision.")
    for issue in report["issues"]:
        issue_lines += ["", f"Issue {issue['issue_id']} | Status: {issue['status']}",
                        *[f"{key}: {value}" for key, value in issue.items() if key not in {"issue_id", "status"}]]
    sections.append(("issues", None, None, "Coordination issues and outstanding review", issue_lines))
    if sum(len(text) for _, _, _, title, lines in sections for text in (title, *lines)) > _MAX_TEXT:
        raise ValueError("Client report text exceeds the one-million-character bound")
    regular, bold = _fonts(font_path)
    _check_glyphs([text for _, _, _, title, lines in sections for text in (title, *lines)], regular)
    pages = []
    for kind, source_id, status, title, entries in sections:
        anchor = _wrap(entries[0], regular, 9.5, _WIDTH - 2 * _MARGIN) if kind == "check" else []
        body_entries = entries[1:] if kind == "check" else entries
        wrapped = [line for entry in body_entries for line in _wrap(entry, regular, 9.5, _WIDTH - 2 * _MARGIN)]
        capacity = int((_BODY_TOP - _BODY_BOTTOM) / 14) - len(anchor)
        if capacity < 1:
            raise ValueError("Check identity exceeds the available client-report page body")
        for offset in range(0, len(wrapped), capacity):
            number = len(pages) + 1
            pages.append(({"page_id": f"CR-{number:04d}", "page_number": number, "kind": kind,
                           "source_id": source_id, "status": status}, title, anchor + wrapped[offset:offset + capacity]))
    if len(pages) > _MAX_PAGES:
        raise ValueError("Client report exceeds the 256-page bound")
    target = source.with_name(f"{source.stem}_report.pdf")
    pdf = canvas.Canvas(str(target), pagesize=A4)
    pdf.setTitle(f"Why this variant: {building.layout.project_name}")
    for row, title, lines in pages:
        pdf.setFont(bold, 17)
        pdf.drawString(_MARGIN, _HEIGHT - 57, "Why this variant")
        pdf.setFont(regular, 9.5)
        pdf.drawString(_MARGIN, _HEIGHT - 77, f"Variant {result.variant} | {row['page_id']}")
        pdf.setFont(bold, 12)
        pdf.drawString(_MARGIN, _HEIGHT - 104, title)
        pdf.setFont(regular, 9.5)
        y = _BODY_TOP
        for line in lines:
            pdf.drawString(_MARGIN, y, line)
            y -= 14
        pdf.setLineWidth(0.5)
        pdf.line(_MARGIN, 80, _WIDTH - _MARGIN, 80)
        pdf.setFont(regular, 8)
        pdf.drawString(_MARGIN, 65, "Engineering acceptance: PENDING")
        pdf.drawRightString(_WIDTH - _MARGIN, 65, f"Page {row['page_number']}/{len(pages)}")
        pdf.drawString(_MARGIN, 48, f"Source SHA256: {digest}")
        pdf.showPage()
    pdf.save()
    return {"format": "FLC-client-report", "version": "0.1", "pdf": target.name,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "source": identity,
            "engineering_acceptance": "PENDING", "pages": [row for row, _, _ in pages]}
