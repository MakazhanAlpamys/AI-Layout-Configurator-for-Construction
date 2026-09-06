# Пакет внешней приёмки — pharma-cleanroom pilot

## Назначение и граница

Пакет собирается для трёх внешних gate’ов из
[`PILOT_ACCEPTANCE_SPEC.md`](PILOT_ACCEPTANCE_SPEC.md): process/QA review,
cleanroom/HVAC review и architectural/BIM review. Он содержит один и тот же
детерминированный результат в пяти проекциях: канонический JSON, DXF, векторный
PDF, IFC4 и BCF 2.1.

Пакет — **auditable early-design coordination package**. Он не является GMP-
или ISO-сертификацией, квалификацией чистых помещений, проектом HVAC, CCS,
рабочей документацией или разрешением на строительство. Статусы `PASS` в нём
означают только то, что прошёл заявленный предикат project policy.

## Состав

Каталог `out/acceptance-2026-09-06/` (не версионируется, воспроизводится
командами ниже):

| Путь | Содержимое |
| --- | --- |
| `bundle/building_01..03.*` | три принятых варианта seed 1: JSON, DXF, PDF, IFC, BCF, coordination JSON |
| `bundle/manifest.json` | параметры поиска, seeds, принятые и отклонённые кандидаты |
| `acceptance-matrix-report.json` | матрица seeds 1/7/42 × 3 варианта, хеши артефактов, cross-seed сверка идентификаторов |
| `environment.json` | версии Python и решающих библиотек локального прогона |
| `review/*.md` | по одному досье на каждый внешний gate для каждого варианта |
| `review/*.artifact-inventory.json` | сущности IFC, слои и блоки DXF, содержимое BCF и SHA-256 файлов варианта |
| `previews/building_0N.pdf.p1.png` | растровые превью листов (200 dpi) для быстрого просмотра |
| `previews/building_0N.dxf.svg` | SVG-рендер DXF нативным бэкендом ezdxf |
| `viewer-qa/screenshots/` | 23 скриншота браузерного QA и пять `*-observations.json` |
| `conflict-fixture/`, `history-fixture/` | **не приёмочные** состояния для проверки viewer, см. [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md) |

## Что уже проверено машинно

Эти проверки выполнены и их не нужно повторять вручную; они не заменяют
экспертную оценку.

| Проверка | Команда | Результат 2026-09-06 |
| --- | --- | --- |
| Регрессия | `python -m unittest discover -s tests` | 134 теста, OK |
| Матрица приёмки | `acceptance-building-matrix ... --seeds 1 7 42 --variants 3` | все девять комплектов приняты, cross-seed identity `PASS` |
| Bundle QA каждого варианта | `qa-building bundle\building_0N.json --profile ...` | PROGRAM/GEOMETRY/EQUIPMENT/FLOW/PROFILE/MANIFEST/DXF/PDF/IFC/BCF/COORDINATION — `PASS` |
| IDS-профиль обмена | `validate bundle\building_0N.ifc --ids ids\layout_baseline.ids` | 13/13 spaces, 50–51/… walls, 12/12 doors, 1/1 window, 13/13 openings — `PASS` |
| Facility policy | внутри `qa-building` | 10 `PASS`, `ZONE_RELATIONS` = `NOT_APPLICABLE`, 0 issues |

Состав IFC каждого варианта: 13 `IfcSpace`, 50–51 `IfcWall`, 12 `IfcDoor`,
1 `IfcWindow`, 13 `IfcOpeningElement` с `IfcRelVoidsElement`/`IfcRelFillsElement`,
90 `IfcRelSpaceBoundary`, 13 `IfcBuildingElementProxy` (5 оборудование +
8 производных маршрутов с `Pset_LayoutFlow`).

DXF-слои: `A-WALL`, `A-DOOR`, `A-WINDOW`, `A-EQUIP`, `A-CLEARANCE`, `A-FLOW`,
`A-AXIS`, `A-DIMS`, `A-TEXT`, `A-TITLE`, `A-LEGEND`.

## Чек-лист: технолог / QA фармпроизводства

Вход: `bundle/building_01.json` (секция `facility_validation`),
`previews/building_01.pdf.p1.png`, `bundle/building_01.coordination.json`.

1. Подтвердить словарь стадий процесса: `raw_material → production → packaging
   → finished_goods` и ветку `production → waste`. Отклонения фиксировать как
   изменение программы, а не как правку геометрии.
2. Подтвердить, что декларированные категории потоков (`material`,
   `dirty_material`, `people`, `finished_goods`, `waste`) достаточны для
   реального процесса.
3. Проверить политику разделения: пары `material`/`dirty_material`,
   `material`/`waste`, `people`/`waste`, `finished_goods`/`waste` считаются
   несовместимыми в общих промежуточных помещениях. Подтвердить или изменить
   этот список.
4. Проверить назначение и площади помещений против реального оборудования и
   численности персонала.
5. Явно зафиксировать исключения: временнóе разделение, процедуры передачи и
   дезинфекции в модели не представлены.
6. Замечания оформлять как BCF-topic (`bundle/building_01.bcf`), а не как
   правку DXF.

## Чек-лист: инженер чистых помещений / HVAC

Вход: `bundle/building_01.json` (секции `spec.zones`, `facility_validation`),
`previews/building_01.pdf.p1.png`.

1. Проверить классы зон и порядок «родитель — потомок» по чистоте;
   инструмент проверяет только словарь и порядок и не рассчитывает
   классификацию.
2. Проверить декларированные перепады давления и применимость guidance-значения
   10 Па из EU GMP Annex 1 к этой стратегии; значение — декларация проекта.
3. Проверить раздельные роли personnel/material airlock, их parent-ссылку на
   cleanroom и членство помещений.
4. Подтвердить, что отсутствие модели door interlocking, воздухообмена,
   расчёта частиц и containment — приемлемая граница для этой стадии.
5. Зафиксировать требования к HVAC-зонам, которые не выражаются текущей
   моделью, как вход для следующего цикла.

## Чек-лист: архитектор / BIM-координатор

Вход: `bundle/building_0N.dxf`, `bundle/building_0N.ifc`,
`bundle/building_0N.pdf`, `bundle/building_0N.bcf`.

1. Открыть DXF в принимающем CAD и IFC в принимающем BIM-инструменте;
   зафиксировать, открылись ли файлы без потерь, и в какой версии инструмента.
2. Проверить слои, блоки оборудования, пунктирные clearance и штамп листа.
3. Проверить IFC: пространственную структуру, стены, проёмы, типы,
   `IfcRelSpaceBoundary` и property sets оборудования и маршрутов.
4. Сравнить три варианта: идентификаторы помещений, оборудования и потоков
   должны совпадать, геометрия — различаться.
5. Учесть, что комплект — один из многих допустимых вариантов. Повторный
   запуск с тем же seed даёт другую принятую геометрию (`RP-01`), поэтому
   проверяйте файлы, зафиксированные хешами в
   `review/*.artifact-inventory.json`, а не пересобранный комплект.
6. Замечания возвращать в `*.bcf`; следующая итерация переносит исчезнувшие
   topics в новый пакет как `Closed`.

## Воспроизведение пакета

```powershell
# 1. Регрессия
.venv\Scripts\python.exe -m unittest discover -s tests

# 2. Матрица приёмки (источник bundle и acceptance-matrix-report.json)
.venv\Scripts\python.exe -m layout_configurator.cli acceptance-building-matrix `
  examples\pharma_cleanroom_pilot.yaml `
  --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\acceptance-2026-09-06\matrix `
  --variants 3 --seeds 1 7 42 --max-attempts 9 --time-limit 30

# 3. Независимая перепроверка каждого варианта
.venv\Scripts\python.exe -m layout_configurator.cli qa-building `
  out\acceptance-2026-09-06\bundle\building_01.json `
  --profile rules\pharma_cleanroom_pilot.yaml

# 4. Проверка IFC по IDS
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\acceptance-2026-09-06\bundle\building_01.ifc --ids ids\layout_baseline.ids

# 5. Read-only review в браузере
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\acceptance-2026-09-06\bundle --variant 1 `
  --profile rules\pharma_cleanroom_pilot.yaml
```

## Статус gate’ов на 2026-09-06

| Gate | Владелец | Статус |
| --- | --- | --- |
| Process and quality review | технолог / QA | **открыт** — пакет и чек-лист готовы, ревью не проводилось |
| Cleanroom/HVAC review | инженер чистых помещений / HVAC | **открыт** — пакет и чек-лист готовы, ревью не проводилось |
| Architectural/BIM review | архитектор / BIM-координатор | **открыт** — DXF/IFC/PDF/BCF готовы и прошли машинный read-back; открытие в принимающих CAD/BIM не выполнено, в окружении их нет |
| Viewer screenshot QA | browser runtime | **закрыт** — скриншоты получены, дефект единиц проекции VQ-01…VQ-04 исправлен и прогон повторён; остаются несблокирующие VQ-05…VQ-12, см. [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md) |
