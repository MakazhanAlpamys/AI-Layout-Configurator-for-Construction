# Передача контекста

## Актуальная продуктовая цель — 2026-09-05

Проект — **Facility Layout Compiler**, а не общий генератор планировок. Целевая
ниша: фармацевтика, cleanrooms, лаборатории, больницы и промышленные объекты,
где проверяемы потоки, clearance оборудования, зоны и evidence важнее красивой
картинки. Первый узкий клин — pharma-like clean production. Полная формулировка
и границы: [FACILITY_PRODUCT_BRIEF.md](FACILITY_PRODUCT_BRIEF.md); технический
план: [PRODUCT_PLAN.md](PRODUCT_PLAN.md).

## Текущий этап и порядок продолжения

Первый приёмочный пример — `examples/pharma_cleanroom_pilot.yaml`: 13 помещений,
6 зон, 5 единиц оборудования, 7 потоков и составной профиль
`rules/pharma_cleanroom_pilot.yaml`. Программа на 20–40 помещений и 10–30 единиц
оборудования в `PRODUCT_PLAN.md` — следующий этап масштаба после его приёмки.

`facility_generation.py` передаёт CP-SAT обязательные/запрещённые связи зон,
минимальную общую границу с запасом на стены и необходимые габариты оборудования.
Независимые geometry/equipment/flow/profile проверки отбирают полный набор
вариантов. Исправлена также длина общей границы в CP-SAT: обе стороны должны
вмещать требуемый проём, в том числе при вложении одной проекции в другую.

`manifest.json` сохраняет настройки поиска, полученные и проверенные кандидаты.
При неполном наборе `generation-failure.json` сохраняет программу, профиль и
причины отказа; ошибка ограниченного поиска не является доказательством
математической невозможности. Матрица сохраняет этот отчёт и не ставит `PASS`
сравнению идентификаторов без успешного QA всех seeds.

Локально прошли 136 тестов через `python -m unittest discover -s tests -v`.
CI описан в `.github/workflows/ci.yml`: тесты на Windows/Linux, затем матрица
из трёх вариантов для seeds 1/7/42 с лимитом 30 секунд на solver и девятью
кандидатами на seed. CI сохраняет артефакты и отчёты при успехе и ошибке.

Коммит `9cd1d27` отправлен в `origin/main` 2026-09-05. Запущенный им workflow
`33979389646` завершился `failure`, **не дойдя до шагов**: GitHub вернул
`The job was not started because recent account payments have failed or your
spending limit needs to be increased`. Это блокировка биллинга аккаунта, а не
дефект кода или workflow; версии `actions/checkout@v7`, `actions/setup-python@v7`
и `actions/upload-artifact@v7` существуют и корректны. Пока биллинг не
восстановлен, CI-подтверждения матрицы на GitHub нет, и его нельзя предъявлять
как evidence.

Полная локальная матрица 2026-09-05 прошла: seeds 1/7/42, по три варианта,
30 секунд на solver, максимум девять кандидатов, два equipment retries.
Все девять комплектов прошли bundle QA и cross-seed identity checks.
Для seeds 1/7/42 проверено соответственно 3/6/5 кандидатов; отклонено 0/3/2.
Отчёт: `out/pharma_cleanroom_stabilized_2026-09-05/acceptance-matrix-report.json`;
рядом `environment.json` с версиями среды. Эти локальные артефакты исключены из
Git; CI формирует и сохраняет свой комплект. Локальная среда: Python 3.11.15,
OR-Tools 9.15.6755, Shapely 2.1.2, IfcOpenShell 0.8.5.

## Пакет приёмки — 2026-09-06

Актуальный пакет внешней приёмки — `out/acceptance-2026-09-06/`
(не версионируется, пересобирается командами из
[ACCEPTANCE_REVIEW_PACKAGE.md](ACCEPTANCE_REVIEW_PACKAGE.md)): три принятых
варианта seed 1 во всех проекциях, матрица seeds 1/7/42 со статусом `PASS`,
досье под каждый внешний gate, превью листов PDF (200 dpi), SVG-рендеры DXF и
23 скриншота браузерного QA.

Каждый из трёх человеческих gate’ов теперь обслуживается сгенерированным досье:
`layout-configurator review-dossier` проецирует принятый комплект в один
документ на роль. Технолог получает декларированные потоки со стадиями,
производные маршруты, политику несовместимых пар и программу помещений;
инженер чистых помещений — зоны с классами и давлениями, каскад и роли шлюзов;
архитектор — состав IFC, слои DXF, оборудование с обслуживающими габаритами и
SHA-256 файлов. В каждом досье есть блок решений и явный список того, что
инструмент не проверял. Модуль — `review.py`, тесты — `tests/test_review.py`.

Машинные проверки пакета: 136 тестов OK; `qa-building` — `PASS` для всех трёх
вариантов; IDS `ids/layout_baseline.ids` — `PASS` для всех трёх IFC.

Пакет 2026-09-05 сохранён как исторический: в нём лежит набор скриншотов viewer
до исправления единиц проекции.

Viewer screenshot QA **выполнен и закрыт** в реальном браузере (Chrome headless
shell + puppeteer-core 23.11.1, Node 22.13.0) — прежняя запись об отсутствии
browser runtime более не актуальна. Подтверждены: рендер трёх вариантов,
подсветка конфликтов маршрута и оборудования, фильтры `ALL`/`OPEN`/`RESOLVED`,
полный lifecycle issue с audit trail, resolved-viewpoints закрытых BCF topics и
read-only guard `HTTP 405`.

Первый прогон показал, что SVG-проекция плана нечитаема. Причина — в
`static/style.css` линейные толщины задавались с `vector-effect:
non-scaling-stroke` и трактовались как экранные пиксели, а текст и радиусы
маркеров — в миллиметрах, причём значения были подобраны наоборот: маршрут
120 px ≈ 7248 мм при декларированных 1200–1800 мм, ось 16 px ≈ 966 мм, подпись
комнаты ≈ 2.98 px, маркер issue ≈ 4.64 px.

Исправлено: `drawPlan` публикует единицу проекции `--u` (пользовательских единиц
на отрисованный CSS-пиксель), CSS задаёт текст как `calc(var(--u) * Npx)`,
маркеры и отступы считаются через `px()`, коридор потока рисуется отдельным
`.flow-corridor` в декларированной ширине с hairline-осевой поверх, шаг сетки
поднимается минимум до 9 px, план перерисовывается при resize, подписи комнат
ужимаются под своё помещение, а отступ подписи потока стал экранным. Измерено
после исправления: коридор 1800 мм (29.8 px) и 1200 мм (19.9 px), осевая 2 px,
ось 1 px, подпись комнаты 13.0 px, маркер 7.0 px. Регрессию держит
`tests/test_ui.py::PlanProjectionUnitTests`. Остаются несблокирующие VQ-05…VQ-12 —
в [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md).

Дефекты относятся к проекции, а не к валидации: значения панели, статусы
проверок, координаты маршрутов и BCF-идентификаторы совпадают с
детерминированным отчётом.

## Оставшиеся условия приёмки пилота

| # | Условие | Владелец | Статус |
| --- | --- | --- | --- |
| 1 | Process and quality review | технолог / QA фармпроизводства | открыто; досье `review/building_0N.technologist.md` готово, ревью не проводилось |
| 2 | Cleanroom/HVAC review | инженер чистых помещений / HVAC | открыто; досье `review/building_0N.cleanroom-hvac.md` готово, ревью не проводилось |
| 3 | Architectural/BIM review: открыть DXF и IFC в принимающих CAD/BIM | архитектор / BIM-координатор | открыто; досье готово, но в окружении нет ни одного CAD/BIM-инструмента, машинный read-back это не заменяет |
| 4 | Зелёный прогон CI-матрицы на GitHub | владелец аккаунта | заблокировано биллингом GitHub Actions |
| 5 | Откалибровать бюджет `--deterministic-budget` под CI и решить, переводить ли матрицу в этот режим | владелец продукта | открыто; сам режим готов |
| 6 | Мелкие дефекты VQ-07…VQ-13 | разработка | открыто, не блокирует |

Закрыты: читаемость SVG-проекции (2026-09-05), наложение подписей на листе
`DR-01`, совпадение идентификаторов BCF-topic `BCF-01` и согласованность
статусов issue `VQ-05`/`VQ-06` (2026-09-06). `DR-02` отозван как ошибка
наблюдения.

### RP-01 — закрыт 2026-09-06 отдельным режимом

`--time-limit` сохранил смысл бюджета по реальному времени. Для воспроизводимости
добавлен `--deterministic-budget UNITS`: машинно-независимый объём работы CP-SAT,
однопоточный поиск, снятый wall-clock cap. Флаг есть у `generate`,
`generate-building` и `acceptance-building-matrix`; `manifest.json` и отчёт
матрицы сохраняют `deterministic_units` и признак `repeatable`.

Одного бюджета оказалось мало. `LayoutIR.relation_pairs` возвращал `set`, и
порядок обхода множества строк зависит от `PYTHONHASHSEED`, который Python
выбирает случайно на каждый процесс. Два процесса с одним seed строили разные
модели, а CP-SAT исследует модель в порядке построения. Внутри одного процесса
эффект не виден, поэтому unit-тест проходил, а реальные запуски расходились.
Метод теперь возвращает отсортированный кортеж; инвариант держит
`tests/test_search_budget.py::ModelOrderStabilityTests`.

Калибровка на машине разработки: ≈ 3.4 с на единицу, 10 единиц ≈ прежние
`--time-limit 30`. Ограничения — единицы не секунды, повторяемость привязана к
версии OR-Tools и не означает оптимальности — описаны в
[DETERMINISTIC_BUDGET.md](DETERMINISTIC_BUDGET.md).

Масштабирование до 20–40 помещений и 10–30 единиц оборудования, а также
laboratory/hospital/industrial packs начинаются только после закрытия
пунктов 1–3.

## Архив исходного MVP

Старый текст ниже — исторический контекст исходного residential MVP. Он полезен
как доказательство принятых архитектурных решений, но не ограничивает новый
facility-focused roadmap.

## Главный принцип

**Solver-first hybrid.** Геометрию считает детерминированный солвер, не нейросеть.
Это консенсус всех 8 независимых research-отчётов, а не предпочтение.

LLM подключается последним (подпроект 5) и **никогда** не выдаёт координаты,
не пишет DXF напрямую и не выносит вердикт по нормам. Причина: ни одна открытая
нейросеть (House-GAN++, HouseDiffusion, Graph2Plan) не гарантирует жёсткие
ограничения — они дают дубли комнат, наложения, площади ±30%.

## Что уже работает

MVP подпроекта 1, коммит `b9badb4`. Подход — CP-SAT rectangle packing:

- `models.py` — `LayoutIR`, единственный источник истины. DXF/PDF — производные проекции
- `solver.py` — OR-Tools CP-SAT: `AddNoOverlap2D`, площадь через `AddMultiplicationEquality`,
  смежности через равенство граней + минимальное перекрытие, вычитаемые зоны контура.
  Варианты — через `AddForbiddenAssignments` на предыдущее решение
- `validation.py` — независимая перепроверка результата солвера, включая достижимость комнат от входа
- `export.py` — DXF (ezdxf, слои `A-WALL`/`A-DOOR`/…) и векторный PDF (ReportLab)
- `cli.py` — `layout-configurator generate spec.yaml --output out --variants N`

Валидация намеренно **не доверяет** солверу и проверяет всё заново. Так и оставить.

## Честный разрыв между планом и кодом

Решение было: «стены с толщиной, зачистка углов, проёмы». В коде стены — это
полилинии комнат с толщиной, выраженной через `lineweight`. Настоящих двойных
линий с зачисткой углов и вырезанием проёмов **пока нет**. Это первое, что стоит
доделать, и это не мелочь: ~2 недели работы, которую `ezdxf` и `ReportLab`
не делают за тебя.

## Принятые решения (не переоткрывать без причины)

| Решение | Почему |
|---|---|
| Одноэтажная жилая планировка | На уровне ядра дом и квартира — одна задача |
| CLI + файлы, без веб-UI | Быстрее всего до работающего результата |
| Контур = bbox минус вычитаемые зоны | Покрывает L-, П-, Т-формы почти бесплатно |
| Коридор задаётся в ТЗ как обычная комната | Меньше магии; солвер лишь проверяет достижимость |
| Проект личный, не на продажу | Лицензии не ограничивают: GPL/AGPL и research-датасеты доступны |
| Только прямоугольные комнаты | Реальное жильё на 90% такое; L-образные — склейкой прямоугольников позже |

## Порядок дальше

Подпункты 2–6 имеют рабочие срезы и тесты. Следующие изменения должны быть
расширениями этих контрактов, а не сменой архитектуры:

2. UI-редактор → 3. IFC/BIM и IDS-профили → 4. KZ ruleset → 5. constrained
LLM-парсер и RAG → 6. multi-floor coordination.

Каждый подпроект — свой цикл: спека → план → реализация.

## Не тратить время (единогласно у всех 8 отчётов)

1. Нейросеть, генерящая DXF/DWG напрямую — формат байт-чувствительный, файлы не откроются
2. Машиночитаемых строительных норм не существует. IDS проверяет данные, не геометрию —
   геометрические правила пишутся руками, по одной юрисдикции
3. Открытая запись DWG: LibreDWG стабилен только до R2000. Экспортировать DXF, конвертировать ODA
4. RAG, «проверяющий» нормы — LLM галлюцинируют именно на числовых порогах.
   RAG находит и цитирует пункт, решение принимает код
5. CP-SAT за ~15–20 комнат начнёт тормозить. Для жилья нормально, для больницы нет
6. Независимый расчёт этажей — несущие стены верхнего повиснут над пустотой.
   Все этажи считать в единой сетке осей

## Current status — 2026-08-31

После базового коммита добавлены реальная стеновая геометрия и editable CLI,
ручные `DoorSpec`/`WindowSpec`, IFC4-экспорт, IDS baseline, deterministic
ruleset и JSON Schema boundary. IFC теперь содержит `IfcRelSpaceBoundary`,
`IfcOpeningElement`, `IfcRelVoidsElement`, `IfcRelFillsElement` и типы
`IfcWallType`/`IfcDoorType`/`IfcWindowType`, связанные через
`IfcRelDefinesByType`; `LayoutIR` остаётся единственным источником геометрии.

Проверено: 52 теста проходят до следующего цикла; загрузчик ruleset теперь
поддерживает относительное `extends`, точечное переопределение правил и provenance
источника; `check --require-provenance` умеет требовать полный audit-набор. KZ уже
выбран и подтверждён по официальному PDF; профиль зафиксирован как частичный и
расширяется только после ручной проверки каждого нового пункта. LLM-парсер ТЗ подключать только после этого контракта и только как
producer канонического JSON. Для этой границы добавлена строгая CLI-нормализация
`normalize`, которая отбрасывает generated data и координаты по JSON Schema.
`generate --strict-input` использует ту же границу перед запуском CP-SAT.
Визуальный DXF-аудит выполнен на свежих basic, KZ-entry и multi-floor файлах через
нативный ezdxf SVG-рендерер: проверены двойные стены, зачистка углов, разрывы под
двери/окна, дуги открывания и читаемость подписей. AutoCAD/LibreCAD/ODA Viewer в
окружении отсутствуют, поэтому это не заменяет открытие файла в полноценном CAD.
Найден и зафиксирован частичный KZ-профиль `rules/kz_sn_3_02_02_2023_partial.yaml`;
он проверяет п. 7.8, п. 6.2.13, п. 6.2.8, п. 6.2.12 и п. 8.19 и намеренно не изображает полный code-check.
Для положительного smoke-test добавлен `examples/kz_daylight.yaml`: generated
layout проходит этот профиль с окнами у living room, kitchen и bedroom.
CLI `edit` теперь также принимает `--rules` и после успешной правки запускает
тот же нормативный post-check; FAIL возвращает код 4, экспорт уже сохранён.
В `LayoutIR` добавлены опциональный `external_entry` и признак комнаты
`is_heated`; внешний вход учитывается в solver, стеновой геометрии и IFC.
CLI-редактор поддерживает `--set-external-entry` и `--remove-external-entry`.
Положительный и отрицательный KZ-примеры проверяют п. 6.2.8; нормативный FAIL
сохраняет экспорт и возвращает код 4.
Локальный UI запускается командой `ui` и использует тот же `EditorState`: браузер
не вычисляет координаты, а сервер применяет только известные typed-команды и
переэкспортирует производные файлы. Опциональный `--rules` показывает в UI
deterministic post-check, включая KZ-профиль и provenance. Drag и resize на SVG
являются только preview; отпускание вызывает ровно одну typed-команду, а не
прямую запись координат в JSON. Кнопки undo/redo работают на серверных снимках
`EditorState`, каждый переход снова экспортирует производные файлы, а новая
команда после undo начинает новую ветку. В UI также появились журнал payload-
команд, сетка по `grid_mm` и размерная подпись во время drag/resize; браузерный
визуальный QA остаётся ручным, так как подключённого browser-коннектора в
текущем окружении нет. KZ ruleset теперь наследует generic baseline, а
`ids/kz_layout_exchange.ids` проверяет IFC-свойства KZ-ориентированного обмена.
`parse-brief` превращает компактное текстовое ТЗ в canonical JSON через Schema
boundary; `parse-llm` — опциональный JSON-only adapter для OpenAI-compatible
endpoint; `cite` только извлекает rule/clause/source и не принимает нормативное
решение. Для многоэтажности `generate-multifloor` фиксирует вертикальные ядра
по одной сетке, проверяет структурные оси и экспортирует общий IFC с этажами и
лестницами. Структурные оси применяются внутри CP-SAT как hard constraints,
а IDS-спецификация без применимых сущностей считается vacuous PASS.
Для следующего продуктового контура добавлены versioned domain rule packs
`rules/pharma_clean_production.yaml`, `rules/cleanroom_pilot.yaml`,
`rules/laboratory_pilot.yaml`, `rules/hospital_pilot.yaml` и
`rules/industrial_pilot.yaml`. Их правила типизированы и несут собственные
`source`, `edition`, `effective_date`, `evidence` и `parameters`; пакет cleanroom
также проверяет vocabulary/order классов зон, роли и parent для airlock и давление
с явно sourced guidance value. Они явно не
выдают GMP или иной регуляторный verdict. `BuildingIR` теперь умеет связывать зоны с hard required и
forbidden adjacency-группами, обязательные потоки автоматически повышают
эффективную ширину дверного проёма, а оборудование и маршруты используют
проверяемые точки доступа. `generate-building` сохраняет единый
`facility_validation` report; `check-building` пересчитывает его независимо из
сохранённого результата. Коммерческий pilot содержит явные process stages и
waste branch with a dedicated `waste_hold` room for waste routing, and the
generated three-variant acceptance set passes deterministic facility/bundle QA;
IFC теперь содержит derived flow route proxies с `Pset_LayoutFlow` и статусом
проверки. Каждый вариант также получает `*.coordination.json` с BCF-like issue
records и `*.bcf` с BCF-XML 2.1 topics/viewpoints и внешними ссылками на
program/DXF/PDF/IFC; DXF/PDF — размеры помещений, sheet metadata и подписи
типов потоков/clear width; schema sidecar находится в
`schemas/coordination_issues.schema.json`; структура профиля проверяется через
`schemas/facility_profile.schema.json`.
После записи IFC выполняется независимый read-back через IfcOpenShell с проверкой
entity counts и flow metadata; результат сохраняется в `manifest.json` как
`ifc_readback`.
Для повторной итерации `generate-building --bcf-input` переносит исчезнувшие
topics в новый BCF как `Closed`, сохраняя issue history без изменения канонического
facility validation report.
Добавлена команда `qa-building`: она в read-only режиме сверяет JSON sidecar,
DXF/PDF, IFC read-back и BCF 2.1 с текущим `BuildingIR` result.
Viewer QA и BCF issue-management workflow добавлены в read-only facility review:
открытые flow/equipment conflicts автоматически подсвечиваются на SVG, BCF
topics нормализуются в `OPEN`/`RESOLVED`, а текущие issues и закрытая history
отображаются через фильтры статуса. BCF viewpoint coordinates восстанавливаются
для визуальной отметки закрытых issues. Для выбранного issue доступны Resolve,
Reopen, comment и assign; audit trail сохраняется в отдельном
`building_01.issue-management.json`, а BCF/coordination JSON и manifest counters
обновляются. Геометрия остаётся read-only: edit/undo/redo/reset — HTTP 405.
Профили pharma и cleanroom теперь версии 0.2: pharma sequence требует тип потока,
обязательность декларации и derived route для каждой стадии, а cleanroom проверяет
классы ISO, parent-child ordering, personnel/material airlock roles, parent link и
10 Pa guidance value из Annex 1. Для отсутствующего входного доказательства
сохраняется `UNKNOWN`; это по-прежнему project policy, а не нормативный verdict.
После закрытия текущего pharma-cleanroom пилота — отдельный
spec → implementation → acceptance цикл для laboratory, hospital и industrial
с подтверждёнными источниками. Актуальный порядок указан в начале документа.
### Facility review surface

The `ui` command now auto-detects a generated `BuildingIR` result (`building_01.json`) and opens a read-only facility review. The server independently recomputes equipment, flow, and facility validation; the SVG projection overlays equipment footprints, service-clearance envelopes, derived flow routes, open conflict markers, and resolved BCF viewpoints. The side panel shows profile evidence, `OPEN`/`RESOLVED` filters, current coordination issues, BCF issue history, and JSON/DXF/PDF/IFC/BCF artifacts. A selected issue can be resolved, reopened, assigned, or commented; the audit trail is stored in `building_01.issue-management.json` and refreshes the BCF/coordination JSON projections. Geometry edit, undo, redo, and reset endpoints still return HTTP 405 in this mode.
