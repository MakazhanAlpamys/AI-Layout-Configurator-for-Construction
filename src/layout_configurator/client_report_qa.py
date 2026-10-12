"""Independent readback of an optional client PDF and its canonical source.

This checks delivered identity, saved evidence and vector content. It does not
recalculate engineering predicates or authenticate a supplied source record.
"""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ContentStream

from .compare import variant_metrics
from .facility import FacilityProfile
from .io import load_building_result


@dataclass(frozen=True)
class ClientReportReadback:
    issues: tuple[dict, ...]
    page_count: int = 0

    @property
    def ok(self):
        return not self.issues

    def to_dict(self):
        return {"scope": "Client PDF identity, saved status/evidence and vector readback; no engineering approval",
                "page_count": self.page_count, "issues": list(self.issues)}


def _compact(value):
    return "".join(value.split())


def validate_client_report(root: str | Path, index, *, source: str | Path) -> ClientReportReadback:
    """Inspect the real PDF; a recomputed hash alone cannot establish consistency."""
    issues = []
    page_count = 0

    def issue(code, message, **details):
        issues.append({"code": code, "message": message, **details})

    try:
        root, source = Path(root).resolve(), Path(source).resolve()
        if not source.is_relative_to(root) or source.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Canonical source is outside the bundle or exceeds 8 MiB")
        building, result, _ = load_building_result(source)
        data = json.loads(source.read_bytes())
        facility = data["facility_validation"]
        profile = FacilityProfile.from_mapping(facility["profile"])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = {"json": source.name, "sha256": digest, "variant": result.variant,
                    "project_name": building.layout.project_name, "revision": profile.drawing.revision,
                    "profile_name": profile.name, "profile_version": profile.version}
        if (not isinstance(index, dict) or set(index) != {"format", "version", "pdf", "sha256", "source", "engineering_acceptance", "pages"}
                or index.get("format") != "FLC-client-report" or index.get("version") != "0.1"
                or index.get("engineering_acceptance") != "PENDING"):
            issue("REPORT_INDEX", "Malformed report index or unsupported acceptance claim")
            return ClientReportReadback(tuple(issues))
        if index.get("source") != expected or type(index.get("source", {}).get("variant")) is not int:
            issue("REPORT_SOURCE", "Report source identity differs from the exact canonical result")
        name = index.get("pdf")
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError("Report PDF requires a relative bundle filename")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".pdf" or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Report PDF is outside the bundle, has a wrong extension or exceeds 16 MiB")
        digest_pdf = index.get("sha256")
        if not isinstance(digest_pdf, str) or not re.fullmatch(r"[0-9a-f]{64}", digest_pdf) or hashlib.sha256(path.read_bytes()).hexdigest() != digest_pdf:
            issue("REPORT_HASH", "Delivered PDF does not match the indexed SHA256")
        rows = index.get("pages")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
            issue("REPORT_INDEX", "Report needs a bounded nonempty page index")
            return ClientReportReadback(tuple(issues))
        rules = {rule["id"]: rule for rule in facility["profile"]["rules"]}
        checks = facility["checks"]
        referenced = {check["rule"]["id"] for check in checks if "rule" in check}
        groups = [("identity", None, None), ("metrics", None, None)]
        groups += [("check", check["id"], check["status"]) for check in checks]
        groups += [("rule", rule_id, None) for rule_id in rules if rule_id not in referenced]
        groups += [("issues", None, None)]
        actual_groups, body = [], {}
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted reports cannot be independently read")
        page_count = len(reader.pages)
        if page_count != len(rows):
            issue("REPORT_PAGE_COUNT", "PDF page count differs from its index")
        for number, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or set(row) != {"page_id", "page_number", "kind", "source_id", "status"}
                    or type(row.get("page_number")) is not int or row["page_number"] != number
                    or row.get("page_id") != f"CR-{number:04d}"):
                issue("REPORT_PAGE_ID", "Malformed or duplicate report page identity", page=number)
                continue
            group = (row["kind"], row["source_id"], row["status"])
            if group not in groups:
                issue("REPORT_STATUS", "Indexed section source/status differs from canonical evidence", page=number)
                continue
            if not actual_groups or actual_groups[-1] != group:
                actual_groups.append(group)
            if number > page_count:
                continue
            page = reader.pages[number - 1]
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not math.isclose(width, 595.27559, abs_tol=0.02) or not math.isclose(height, 841.88976, abs_tol=0.02) or page.rotation != 0 or page.user_unit != 1:
                issue("REPORT_PAGE_SIZE", "Report page must be unrotated A4 in points", page=number)
            if page.images:
                issue("REPORT_RASTER", "Client report contains raster image content", page=number)
            stream = ContentStream(page.get_contents(), reader)
            if len(stream.get_data()) > 1024 * 1024:
                raise ValueError("Report page stream exceeds its bound")
            operators = {op for _, op in stream.operations}
            if not {b"l", b"S"} <= operators or not {b"Tj", b"Tf"} <= operators:
                issue("REPORT_VECTOR", "Report lacks readable text or stroked vector content", page=number)
            fragments = []

            def visit(text, cm, tm, font, size):
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                if text.strip():
                    fragments.append((x, y, text))

            page.extract_text(visitor_text=visit)
            header = _compact(" ".join(text for _, y, text in fragments if y >= height - 90))
            footer = _compact(" ".join(text for _, y, text in fragments if y <= 80))
            if "Whythisvariant" not in header or _compact(f"Variant {result.variant} | {row['page_id']}") not in header:
                issue("REPORT_IDENTITY", "Printed page ID or source variant differs from the index", page=number)
            for required in ("Engineering acceptance: PENDING", f"Page {number}/{page_count}", f"Source SHA256: {digest}"):
                if _compact(required) not in footer:
                    issue("REPORT_IDENTITY", "Printed source, page count or acceptance scope differs", page=number)
            body.setdefault(group, []).extend(text for _, y, text in fragments if 100 <= y <= height - 110)
        if actual_groups != groups:
            issue("REPORT_SECTIONS", "Report omits, repeats or reorders a required source-evidence section")

        def require(group, expected_texts):
            text = _compact(" ".join(body.get(group, [])))
            for fragment in expected_texts:
                if _compact(fragment) not in text:
                    issue("REPORT_EVIDENCE", "Printed report omits or changes canonical source evidence", section=group[0], source_id=group[1])
                    break
            return text

        require(groups[0], [f"Project: {expected['project_name']}", f"Drawing revision: {expected['revision']}",
                            f"Profile: {profile.name} / {profile.version}", digest,
                            json.dumps(facility["profile"]["provenance"], sort_keys=True, ensure_ascii=False),
                            "not a permit", "Engineering acceptance: PENDING"])
        metrics = variant_metrics(source)
        require(groups[1], [f"Rooms: {metrics.rooms}", f"Total area deviation: {metrics.area_deviation_pct:.2f} %",
                            f"Worst room deviation: {metrics.max_room_deviation_pct:.2f} % ({metrics.max_room_deviation_room or 'none'})",
                            f"Total derived route length: {metrics.route_length_m:.1f} m",
                            f"Derived door crossings: {metrics.door_crossings}",
                            f"Boundary footprint ratio: {metrics.footprint_ratio:.3f}",
                            *[f"Route type {kind}: {length:.1f} m" for kind, length in sorted(metrics.route_length_by_type_m.items())],
                            f"Saved facility gate outcome: {metrics.gate_status}",
                            f"Failed checks: {len(metrics.failed_checks)}",
                            f"Unknown checks: {len(metrics.unknown_checks)}", f"Open coordination issues: {metrics.open_issues}"])

        def rule_evidence(rule):
            return [f"Declared rule: {rule['id']}", f"Rule kind: {rule['kind']}", f"Rule title: {rule['title']}",
                    f"Rule source: {rule['source']}", f"Edition: {rule['edition']}", f"Effective date: {rule['effective_date']}",
                    f"Severity: {rule['severity']}", *rule["evidence"],
                    json.dumps(rule["parameters"], sort_keys=True, ensure_ascii=False, allow_nan=False)]

        for check in checks:
            group = ("check", check["id"], check["status"])
            expected_text = [f"Title: {check['title']}", *check["evidence"]]
            if "rule" in check:
                expected_text += rule_evidence(rules[check["rule"]["id"]])
            text = require(group, expected_text)
            claims = re.findall(re.escape(_compact(f"Check {check['id']} | Status:")) + r"(NOT_APPLICABLE|UNKNOWN|PASS|FAIL)", text)
            continuations = sum((row.get("kind"), row.get("source_id"), row.get("status")) == group
                                for row in rows if isinstance(row, dict))
            if claims != [check["status"]] * continuations:
                issue("REPORT_STATUS", "Printed check status differs from canonical evidence", source_id=check["id"])
        for rule_id, rule in rules.items():
            if rule_id not in referenced:
                require(("rule", rule_id, None), rule_evidence(rule))
        require(groups[-1], [f"Open coordination issues: {metrics.open_issues}", *[
            value for record in facility["issues"] for value in (
                f"Issue {record['issue_id']} | Status: {record['status']}",
                *[f"{key}: {value}" for key, value in record.items() if key not in {"issue_id", "status"}])]])
    except (OSError, PyPdfError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        issue("REPORT_READBACK", f"Report source or PDF readback failed: {exc}")
    return ClientReportReadback(tuple(issues), page_count)
