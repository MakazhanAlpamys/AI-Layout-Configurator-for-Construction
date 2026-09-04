"""File-based BCF 2.1 exchange for facility coordination issues.

The compiler keeps its deterministic JSON sidecar as the easiest machine-readable
projection, and emits this module's ``.bcf`` ZIP as the interoperable exchange
projection.  The package deliberately contains coordination topics and
viewpoints only; it does not claim that a layout is a regulatory approval.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import base64
import re
import xml.etree.ElementTree as ET

from .facility import CoordinationIssue, FacilityValidationReport


BCF_VERSION = "2.1"
_BCF_EXTENSION_SCHEMA = "https://raw.githubusercontent.com/buildingSMART/BCF-XML/release_2_1/Extension%20Schemas/extensions.xsd"
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class BcfTopicSummary:
    """Small read-back representation of one BCF topic."""

    topic_id: str
    title: str
    status: str
    issue_id: str | None
    viewpoint_id: str | None
    has_snapshot: bool
    code: str | None = None
    message: str = ""
    severity: str = "WARNING"


@dataclass(frozen=True)
class BcfPackageSummary:
    """Validated structural summary of a BCF 2.1 ZIP package."""

    path: Path
    version: str
    topics: tuple[BcfTopicSummary, ...]

    @property
    def open_topics(self) -> int:
        return sum(topic.status.lower() == "open" for topic in self.topics)

    @property
    def resolved_topics(self) -> int:
        return sum(topic.status.lower() in {"closed", "resolved"} for topic in self.topics)


def merge_bcf_history(
    report: FacilityValidationReport,
    previous: BcfPackageSummary | str | Path | None,
) -> FacilityValidationReport:
    """Carry disappeared topics forward as resolved history.

    Current deterministic findings always win and are emitted as their current
    status. Topics that existed in a previous package but are absent from the
    new validation report remain in the package as ``RESOLVED`` records.
    """

    if previous is None:
        return report
    previous_summary = previous if isinstance(previous, BcfPackageSummary) else read_bcf_package(previous)
    current_ids = {issue.issue_id for issue in report.issues}
    historical: list[CoordinationIssue] = []
    for topic in previous_summary.topics:
        if not topic.issue_id or topic.issue_id in current_ids:
            continue
        historical.append(
            CoordinationIssue(
                issue_id=topic.issue_id,
                code=topic.code or "BCF_IMPORTED",
                title=topic.title,
                message=topic.message or "Issue no longer appears in the current facility validation report",
                severity=topic.severity if topic.severity in {"ERROR", "WARNING", "INFO"} else "WARNING",
                source="BCF_HISTORY",
                status="RESOLVED",
            )
        )
    if not historical:
        return report
    return replace(report, issues=tuple(report.issues) + tuple(historical))


def write_bcf_package(
    path: str | Path,
    report: FacilityValidationReport,
    *,
    project_name: str | None = None,
    variant: int | None = None,
    model_references: Mapping[str, str] | None = None,
    ifc_path: str | Path | None = None,
    previous: BcfPackageSummary | str | Path | None = None,
) -> BcfPackageSummary:
    """Write a BCF-XML 2.1 ``.bcf`` ZIP and read it back for structural QA.

    BCF XML 2.1 stores one topic in a UUID directory containing ``markup.bcf``
    and one or more ``*.bcfv`` viewpoints.  The generated model links are
    external references, so the package remains small and does not duplicate
    the IFC/DXF/PDF deliverables beside it.
    """

    report = merge_bcf_history(report, previous)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    name = project_name or "Facility Layout Compiler"
    references = {str(key): str(value) for key, value in (model_references or {}).items() if value}
    ifc_guid = _read_ifc_project_guid(ifc_path)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    entries: list[tuple[str, bytes]] = []
    entries.append(("bcf.version", _version_xml().encode("utf-8")))
    entries.append(("project.bcfp", _project_xml(name, variant).encode("utf-8")))

    for index, issue in enumerate(report.issues):
        topic_id = _topic_uuid(issue.issue_id)
        viewpoint_id = _viewpoint_uuid(issue.issue_id)
        topic_dir = topic_id
        viewpoint_name = f"{viewpoint_id}.bcfv"
        entries.append(
            (
                f"{topic_dir}/markup.bcf",
                _markup_xml(
                    issue,
                    topic_id=topic_id,
                    viewpoint_id=viewpoint_id,
                    viewpoint_name=viewpoint_name,
                    model_references=references,
                    ifc_guid=ifc_guid,
                    created_at=created_at,
                    index=index,
                ).encode("utf-8"),
            )
        )
        entries.append(
            (
                f"{topic_dir}/{viewpoint_name}",
                _viewpoint_xml(viewpoint_id, issue.location).encode("utf-8"),
            )
        )
        entries.append((f"{topic_dir}/snapshot.png", _PNG_1X1))

    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for entry_name, data in entries:
            info = ZipInfo(entry_name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return read_bcf_package(output)


def read_bcf_package(path: str | Path) -> BcfPackageSummary:
    """Read a BCF 2.1 package and verify the required topic relationships."""

    source = Path(path)
    with ZipFile(source) as archive:
        names = set(archive.namelist())
        if "bcf.version" not in names:
            raise ValueError("BCF package is missing bcf.version")
        version_root = ET.fromstring(archive.read("bcf.version"))
        if version_root.tag != "Version":
            raise ValueError("BCF version document has an unexpected root element")
        version = version_root.attrib.get("VersionId", "")
        if version != BCF_VERSION:
            raise ValueError(f"Unsupported BCF XML version: {version or 'missing'}")

        topics: list[BcfTopicSummary] = []
        for markup_name in sorted(name for name in names if name.endswith("/markup.bcf")):
            topic_dir = markup_name.split("/", 1)[0]
            if not _GUID_RE.match(topic_dir):
                raise ValueError(f"BCF topic directory is not a UUID: {topic_dir}")
            root = ET.fromstring(archive.read(markup_name))
            topic = root.find("Topic")
            if topic is None:
                raise ValueError(f"BCF markup has no Topic: {markup_name}")
            topic_id = topic.attrib.get("Guid", "")
            if topic_id.lower() != topic_dir.lower() or not _GUID_RE.match(topic_id):
                raise ValueError(f"BCF topic GUID mismatch: {markup_name}")
            viewpoint = root.find("Viewpoints")
            viewpoint_id = None if viewpoint is None else viewpoint.attrib.get("Guid")
            if viewpoint is not None:
                if not viewpoint_id or not _GUID_RE.match(viewpoint_id):
                    raise ValueError(f"BCF viewpoint has an invalid GUID: {markup_name}")
                viewpoint_file = viewpoint.findtext("Viewpoint")
                if viewpoint_file and f"{topic_dir}/{viewpoint_file}" not in names:
                    raise ValueError(f"BCF viewpoint file is missing: {viewpoint_file}")
                if viewpoint_file:
                    viewpoint_root = ET.fromstring(archive.read(f"{topic_dir}/{viewpoint_file}"))
                    if viewpoint_root.tag != "VisualizationInfo":
                        raise ValueError(f"BCF viewpoint has an unexpected root element: {viewpoint_file}")
                    if viewpoint_root.attrib.get("Guid", "").lower() != viewpoint_id.lower():
                        raise ValueError(f"BCF viewpoint GUID mismatch: {viewpoint_file}")
                snapshot_file = viewpoint.findtext("Snapshot")
                if snapshot_file and f"{topic_dir}/{snapshot_file}" not in names:
                    raise ValueError(f"BCF snapshot file is missing: {snapshot_file}")
            labels = [value.text or "" for value in topic.findall("Labels")]
            issue_id = next((value for value in labels if value.startswith("FLC-")), None)
            code = next((value for value in labels if value and not value.startswith("FLC-")), None)
            priority = topic.findtext("Priority", "Normal")
            topics.append(
                BcfTopicSummary(
                    topic_id=topic_id,
                    title=topic.findtext("Title", ""),
                    status=topic.attrib.get("TopicStatus", "Open"),
                    issue_id=issue_id,
                    viewpoint_id=viewpoint_id,
                    has_snapshot=bool(viewpoint is not None and viewpoint.findtext("Snapshot")),
                    code=code,
                    message=topic.findtext("Description", ""),
                    severity={"High": "ERROR", "Normal": "WARNING", "Low": "INFO"}.get(priority, "WARNING"),
                )
            )
    return BcfPackageSummary(path=source, version=version, topics=tuple(topics))


def _version_xml() -> str:
    root = ET.Element("Version", {"VersionId": BCF_VERSION})
    ET.SubElement(root, "DetailedVersion").text = BCF_VERSION
    return _xml_bytes(root).decode("utf-8")


def _project_xml(project_name: str, variant: int | None) -> str:
    project_id = str(uuid5(NAMESPACE_URL, f"{project_name}:{variant or 0}"))
    root = ET.Element("ProjectExtension")
    project = ET.SubElement(root, "Project", {"ProjectId": project_id})
    ET.SubElement(project, "Name").text = project_name
    extension_schema = ET.SubElement(root, "ExtensionSchema")
    extension_schema.text = _BCF_EXTENSION_SCHEMA
    return _xml_bytes(root).decode("utf-8")


def _markup_xml(
    issue: CoordinationIssue,
    *,
    topic_id: str,
    viewpoint_id: str,
    viewpoint_name: str,
    model_references: Mapping[str, str],
    ifc_guid: str | None,
    created_at: str,
    index: int,
) -> str:
    root = ET.Element("Markup")
    if model_references or ifc_guid:
        header = ET.SubElement(root, "Header")
        for key, filename in sorted(model_references.items()):
            attributes = {"isExternal": "true"}
            if key == "ifc" and ifc_guid:
                attributes["IfcProject"] = ifc_guid
            file_node = ET.SubElement(header, "File", attributes)
            ET.SubElement(file_node, "Filename").text = filename
            ET.SubElement(file_node, "Reference").text = filename

    topic = ET.SubElement(
        root,
        "Topic",
        {
            "Guid": topic_id,
            "TopicType": issue.source,
            "TopicStatus": "Closed" if issue.status == "RESOLVED" else "Open",
        },
    )
    ET.SubElement(topic, "Title").text = issue.title
    ET.SubElement(topic, "Priority").text = _priority(issue.severity)
    ET.SubElement(topic, "Index").text = str(index)
    ET.SubElement(topic, "Labels").text = issue.issue_id
    ET.SubElement(topic, "Labels").text = issue.code
    ET.SubElement(topic, "CreationDate").text = created_at
    ET.SubElement(topic, "CreationAuthor").text = "layout-configurator"
    ET.SubElement(topic, "Description").text = issue.message
    for key, filename in sorted(model_references.items()):
        document = ET.SubElement(topic, "DocumentReference", {"Guid": str(uuid5(NAMESPACE_URL, f"{topic_id}:{key}")), "isExternal": "true"})
        ET.SubElement(document, "ReferencedDocument").text = filename
        ET.SubElement(document, "Description").text = f"{key} deliverable"

    viewpoints = ET.SubElement(root, "Viewpoints", {"Guid": viewpoint_id})
    ET.SubElement(viewpoints, "Viewpoint").text = viewpoint_name
    ET.SubElement(viewpoints, "Snapshot").text = "snapshot.png"
    ET.SubElement(viewpoints, "Index").text = "0"
    return _xml_bytes(root).decode("utf-8")


def _viewpoint_xml(viewpoint_id: str, location: tuple[float, float] | None) -> str:
    x_mm, y_mm = location or (0.0, 0.0)
    root = ET.Element("VisualizationInfo", {"Guid": viewpoint_id})
    camera = ET.SubElement(root, "OrthogonalCamera")
    point = ET.SubElement(camera, "CameraViewPoint")
    _point(point, x_mm / 1000.0, y_mm / 1000.0, 10.0)
    direction = ET.SubElement(camera, "CameraDirection")
    _point(direction, 0.0, 0.0, -1.0)
    up = ET.SubElement(camera, "CameraUpVector")
    _point(up, 0.0, 1.0, 0.0)
    ET.SubElement(camera, "ViewToWorldScale").text = "25.0"
    return _xml_bytes(root).decode("utf-8")


def _point(parent: ET.Element, x: float, y: float, z: float) -> None:
    ET.SubElement(parent, "X").text = f"{x:.6f}"
    ET.SubElement(parent, "Y").text = f"{y:.6f}"
    ET.SubElement(parent, "Z").text = f"{z:.6f}"


def _xml_bytes(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _topic_uuid(issue_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"layout-configurator:bcf-topic:{issue_id}"))


def _viewpoint_uuid(issue_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"layout-configurator:bcf-viewpoint:{issue_id}"))


def _priority(severity: str) -> str:
    return {"ERROR": "High", "WARNING": "Normal", "INFO": "Low"}.get(severity, "Normal")


def _read_ifc_project_guid(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        import ifcopenshell

        document = ifcopenshell.open(str(path))
        project = next(iter(document.by_type("IfcProject")), None)
        guid = None if project is None else getattr(project, "GlobalId", None)
        return guid if guid and len(guid) == 22 else None
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
