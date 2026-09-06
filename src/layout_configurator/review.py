"""Reviewer dossiers for the external acceptance gates.

`PILOT_ACCEPTANCE_SPEC.md` keeps three gates that no test can close: a process
and quality review, a cleanroom/HVAC review and an architectural/BIM review.
Those reviewers should be deciding, not mining JSON, so this module projects an
accepted bundle into one document per role: the declarations that role is asked
to confirm, the deterministic evidence already produced, and the explicit list of
what the tool did not evaluate.

A dossier is a projection like DXF, PDF and IFC are. It states nothing the
canonical result does not already contain and it never turns a project policy
`PASS` into a regulatory verdict.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import ezdxf
import ifcopenshell

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, clearance_rect
from .facility import FacilityProfile, FacilityValidationReport
from .flows import FlowRoutingResult
from .models import LayoutResult

BOUNDARY_NOTE = (
    "Этот пакет — auditable early-design coordination package. Он не является "
    "GMP- или ISO-сертификацией, квалификацией чистых помещений, проектом HVAC, "
    "contamination control strategy, рабочей документацией или разрешением на "
    "строительство. `PASS` означает только то, что прошёл заявленный предикат "
    "project policy."
)

RETURN_NOTE = (
    "Замечания возвращайте как BCF-topic в `{stem}.bcf` либо списком с указанием "
    "id помещения, оборудования или потока. Следующая итерация переносит "
    "исчезнувшие из отчёта topics в новый пакет как `Closed`, поэтому история "
    "замечаний сохраняется. Правки в DXF/PDF не попадают обратно в модель: "
    "источником истины остаётся программа объекта."
)

IFC_ENTITIES = (
    "IfcProject",
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
    "IfcSpace",
    "IfcWall",
    "IfcDoor",
    "IfcWindow",
    "IfcOpeningElement",
    "IfcRelVoidsElement",
    "IfcRelFillsElement",
    "IfcRelSpaceBoundary",
    "IfcBuildingElementProxy",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return "_Нет записей._\n"
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _status_rows(report: FacilityValidationReport) -> list[list[str]]:
    return [[check.id, check.status, "; ".join(check.evidence)] for check in report.checks]


def _header(title: str, building: BuildingIR, result: LayoutResult, profile: FacilityProfile) -> str:
    return (
        f"# {title}\n\n"
        f"**Объект:** {building.layout.project_name}  \n"
        f"**Вариант:** {result.variant}  \n"
        f"**Профиль:** {profile.name} (`{profile.domain}`, версия {profile.version})  \n"
        f"**Юрисдикция профиля:** {profile.jurisdiction}\n\n"
        f"{BOUNDARY_NOTE}\n\n"
    )


def _decision_block(items: Sequence[str], stem: str) -> str:
    lines = ["## Что требуется от рецензента\n"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(items, start=1))
    lines.append("")
    lines.append("Для каждого пункта укажите один из вариантов: **подтверждаю**, "
                 "**требуется изменение программы**, **исключение зафиксировано**.")
    lines.append("")
    lines.append(RETURN_NOTE.format(stem=stem))
    lines.append("")
    return "\n".join(lines)


def _out_of_scope(items: Sequence[str]) -> str:
    lines = ["## Вне области проверки инструмента\n"]
    lines.extend(f"- {text}" for text in items)
    lines.append("")
    return "\n".join(lines)


def technologist_dossier(
    building: BuildingIR,
    result: LayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
) -> str:
    routed = {route.flow_id for route in routes.routes}
    flow_rows = [
        [
            flow.id,
            flow.type,
            flow.stage or "—",
            "да" if flow.required else "нет",
            f"{flow.minimum_clear_width_mm:.0f}",
            ", ".join(flow.from_ids),
            ", ".join(flow.to_ids),
            "да" if flow.id in routed else "нет",
        ]
        for flow in building.flows
    ]
    route_rows = [
        [route.flow_id, route.from_id, route.to_id, " → ".join(route.room_path), f"{route.minimum_clear_width_mm:.0f}"]
        for route in routes.routes
    ]
    room_rows = [
        [
            room.id,
            room.type,
            f"{result.placements[room.id].area_m2:.1f}",
            f"{room.min_area_m2:.0f}–{room.max_area_m2:.0f}",
            f"{result.placements[room.id].width:.0f} × {result.placements[room.id].height:.0f}",
        ]
        for room in building.layout.rooms
    ]
    pair_rows = [[first, second] for first, second in profile.incompatible_flow_type_pairs]

    return (
        _header("Досье: технолог / QA фармпроизводства", building, result, profile)
        + "## Декларированные потоки\n\n"
        + _table(
            ["Поток", "Тип", "Стадия", "Обязательный", "Ширина, мм", "Откуда", "Куда", "Маршрут построен"],
            flow_rows,
        )
        + "\n## Производные маршруты\n\n"
        + _table(["Поток", "Начало", "Конец", "Через помещения", "Ширина, мм"], route_rows)
        + "\n## Политика разделения потоков в профиле\n\n"
        + "Пары типов, которые профиль считает несовместимыми в общих промежуточных помещениях:\n\n"
        + _table(["Тип A", "Тип B"], pair_rows)
        + "\n## Программа помещений\n\n"
        + _table(["Помещение", "Тип", "Площадь, м²", "Допуск, м²", "Габарит, мм"], room_rows)
        + "\n## Детерминированные проверки\n\n"
        + _table(["Проверка", "Статус", "Evidence"], _status_rows(report))
        + "\n"
        + _decision_block(
            [
                "Подтвердить последовательность стадий и ветку отходов как контракт процесса.",
                "Подтвердить, что перечень типов потоков покрывает реальный процесс.",
                "Подтвердить или изменить список несовместимых пар типов потоков.",
                "Подтвердить назначение и площади помещений против реального оборудования и численности персонала.",
                "Зафиксировать исключения, которые модель не выражает.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "временнóе разделение потоков и организационные процедуры;",
                "процедуры передачи, дезинфекции и очистки;",
                "валидация процесса, стерилизации и очистки;",
                "contamination control strategy и её одобрение.",
            ]
        )
    )


def cleanroom_dossier(
    building: BuildingIR,
    result: LayoutResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
) -> str:
    zone_rows = [
        [
            zone.id,
            zone.type,
            zone.cleanroom_class or "—",
            "—" if zone.pressure_pa is None else f"{zone.pressure_pa:.0f}",
            zone.parent_zone_id or "—",
            "да" if zone.airlock else "нет",
            ", ".join(zone.room_ids),
        ]
        for zone in building.zones
    ]
    cascade_rows = []
    zones_by_id = {zone.id: zone for zone in building.zones}
    for zone in building.zones:
        parent = zones_by_id.get(zone.parent_zone_id or "")
        if parent is None or zone.pressure_pa is None or parent.pressure_pa is None:
            continue
        cascade_rows.append(
            [
                f"{zone.id} → {parent.id}",
                f"{zone.pressure_pa:.0f} → {parent.pressure_pa:.0f}",
                f"{parent.pressure_pa - zone.pressure_pa:.0f}",
            ]
        )
    airlock_rows = [
        [zone.id, zone.type, zone.parent_zone_id or "—", ", ".join(zone.room_ids)]
        for zone in building.zones
        if zone.airlock
    ]
    unknown = [check for check in report.checks if check.status == "UNKNOWN"]

    return (
        _header("Досье: инженер чистых помещений / HVAC", building, result, profile)
        + "## Зоны и декларированные параметры\n\n"
        + _table(
            ["Зона", "Тип", "Класс", "Давление, Па", "Родитель", "Airlock", "Помещения"],
            zone_rows,
        )
        + "\n## Заявленный каскад давлений\n\n"
        + "Значения — декларация проекта. Инструмент проверяет только порядок и "
        + "наличие полей и не рассчитывает воздухообмен.\n\n"
        + _table(["Переход", "Па", "Перепад, Па"], cascade_rows)
        + "\n## Шлюзы\n\n"
        + _table(["Зона", "Роль", "Родительская чистая зона", "Помещения"], airlock_rows)
        + "\n## Детерминированные проверки\n\n"
        + _table(["Проверка", "Статус", "Evidence"], _status_rows(report))
        + (
            "\n**Внимание:** есть проверки со статусом `UNKNOWN` — "
            + ", ".join(check.id for check in unknown)
            + ". `UNKNOWN` означает нехватку входных данных и не является подтверждением.\n"
            if unknown
            else ""
        )
        + "\n"
        + _decision_block(
            [
                "Подтвердить классы зон и порядок «родитель — потомок» по чистоте.",
                "Подтвердить декларированные перепады давления и применимость guidance-значения профиля к этой стратегии.",
                "Подтвердить раздельные роли personnel/material airlock и их привязку к чистой зоне.",
                "Подтвердить, что отсутствие модели door interlocking, воздухообмена, расчёта частиц и containment приемлемо на этой стадии.",
                "Зафиксировать требования к HVAC-зонам, которые текущая модель не выражает.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "расчёт воздухообмена, кратностей и перепадов давления;",
                "моделирование воздушных потоков и счёта частиц;",
                "логика блокировок дверей шлюзов;",
                "подбор оборудования HVAC и containment;",
                "квалификация чистых помещений по ISO 14644.",
            ]
        )
    )


def architect_dossier(
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
    artifacts: Mapping[str, Path],
    inventory: Mapping[str, object],
) -> str:
    specs = {item.id: item for item in building.equipment}
    equipment_rows = []
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        clearance = clearance_rect(spec, placement.rect, placement.rotated) if spec else placement.rect
        equipment_rows.append(
            [
                placement.equipment_id,
                spec.type if spec else "—",
                placement.room_id,
                f"{placement.rect.width:.0f} × {placement.rect.height:.0f}",
                f"{clearance.width:.0f} × {clearance.height:.0f}",
                "да" if placement.rotated else "нет",
            ]
        )
    grid = building.structural_grid
    grid_text = (
        "оси X: " + ", ".join(f"{label} ({axis:.0f})" for label, axis in zip(grid.labels_x, grid.axes_x_mm))
        + "  \nоси Y: " + ", ".join(f"{label} ({axis:.0f})" for label, axis in zip(grid.labels_y, grid.axes_y_mm))
        if grid
        else "конструктивная сетка не задана"
    )
    artifact_rows = [
        [path.name, f"{path.stat().st_size / 1024:.1f} КБ", _sha256(path)[:16] + "…"]
        for _, path in sorted(artifacts.items())
        if path.is_file()
    ]
    ifc_rows = [[entity, str(count)] for entity, count in (inventory.get("ifc_entities") or {}).items()]
    dxf_rows = [[layer, str(count)] for layer, count in (inventory.get("dxf_layers") or {}).items()]
    drawing = profile.drawing

    return (
        _header("Досье: архитектор / BIM-координатор", building, result, profile)
        + "## Лист\n\n"
        + f"Штамп `{drawing.sheet_id} Rev {drawing.revision}`, дисциплина {drawing.discipline}, "
        + f"заголовок «{drawing.title}».\n\n"
        + "## Конструктивные оси\n\n"
        + grid_text
        + "\n\n## Оборудование и обслуживающие габариты\n\n"
        + _table(
            ["Оборудование", "Тип", "Помещение", "Габарит, мм", "С учётом clearance, мм", "Повёрнуто"],
            equipment_rows,
        )
        + "\n## Состав IFC\n\n"
        + _table(["Сущность", "Количество"], ifc_rows)
        + "\n## Слои DXF\n\n"
        + _table(["Слой", "Сущностей"], dxf_rows)
        + "\n## Файлы комплекта\n\n"
        + _table(["Файл", "Размер", "SHA-256"], artifact_rows)
        + "\n## Детерминированные проверки\n\n"
        + _table(["Проверка", "Статус", "Evidence"], _status_rows(report))
        + "\n"
        + _decision_block(
            [
                "Открыть DXF в принимающем CAD и IFC в принимающем BIM-инструменте; зафиксировать инструмент, версию и потери при открытии.",
                "Проверить слои, блоки оборудования, пунктирные clearance и штамп листа.",
                "Проверить пространственную структуру IFC, стены, проёмы, типы, `IfcRelSpaceBoundary` и property sets оборудования и маршрутов.",
                "Сравнить варианты комплекта: идентификаторы помещений, оборудования и потоков должны совпадать, геометрия — различаться.",
                "Зафиксировать требования к оформлению, которые текущая проекция не выполняет.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "рабочая документация и узлы;",
                "конструктивный расчёт и MEP-разводка;",
                "пожарные, эвакуационные и доступностные требования;",
                "согласование и разрешение на строительство.",
            ]
        )
    )


def artifact_inventory(artifacts: Mapping[str, Path]) -> dict[str, object]:
    """Read back the exchange artifacts of one variant for the architect dossier."""

    inventory: dict[str, object] = {}
    ifc_path = artifacts.get("ifc")
    if ifc_path is not None and ifc_path.is_file():
        model = ifcopenshell.open(str(ifc_path))
        inventory["ifc_schema"] = model.schema
        inventory["ifc_entities"] = {entity: len(model.by_type(entity)) for entity in IFC_ENTITIES}
    dxf_path = artifacts.get("dxf")
    if dxf_path is not None and dxf_path.is_file():
        document = ezdxf.readfile(str(dxf_path))
        layers = Counter(entity.dxf.layer for entity in document.modelspace())
        inventory["dxf_version"] = document.dxfversion
        inventory["dxf_layers"] = dict(sorted(layers.items()))
        inventory["dxf_blocks"] = sorted(
            block.name for block in document.blocks if not block.name.startswith("*")
        )
    bcf_path = artifacts.get("bcf")
    if bcf_path is not None and bcf_path.is_file():
        with zipfile.ZipFile(bcf_path) as archive:
            names = archive.namelist()
        inventory["bcf_topics"] = sum(name.endswith("markup.bcf") for name in names)
        inventory["bcf_viewpoints"] = sum(name.endswith(".bcfv") for name in names)
    inventory["sha256"] = {
        path.name: _sha256(path) for _, path in sorted(artifacts.items()) if path.is_file()
    }
    return inventory


def write_review_dossier(
    directory: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    *,
    source: Path,
) -> dict[str, Path]:
    """Write one dossier per external gate plus the artifact inventory."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    artifacts = {
        suffix.lstrip("."): source.with_suffix(suffix)
        for suffix in (".json", ".dxf", ".pdf", ".ifc", ".bcf")
    }
    inventory = artifact_inventory(artifacts)

    written: dict[str, Path] = {}
    documents = {
        "technologist.md": technologist_dossier(building, result, routes, report, profile, stem),
        "cleanroom-hvac.md": cleanroom_dossier(building, result, report, profile, stem),
        "architect-bim.md": architect_dossier(
            building, result, equipment, routes, report, profile, stem, artifacts, inventory
        ),
    }
    for name, text in documents.items():
        path = target / f"{stem}.{name}"
        path.write_text(text, encoding="utf-8")
        written[name] = path

    inventory_path = target / f"{stem}.artifact-inventory.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    written["artifact-inventory.json"] = inventory_path
    return written
