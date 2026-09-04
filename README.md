# Facility Layout Compiler

## Domain rule packs

Правила для regulated facilities задаются YAML-профилями, а не зашиваются в
валидатор. Пакеты `cleanroom`, `pharma`, `laboratory`, `hospital` и `industrial`
содержат типизированные правила потоков и собственные `source`, `edition`,
`effective_date`, `evidence` и `parameters`.

Структура профиля проверяется схемой
[`schemas/facility_profile.schema.json`](schemas/facility_profile.schema.json).
Результаты попадают в `facility_validation` и BCF-like issues вместе с ссылкой
на применённое правило. Если входных данных недостаточно, результатом является
`UNKNOWN`; система не подставляет нормативные числа и не выдаёт regulatory verdict.

Solver-first компилятор проверяемых планировок для регулируемых и
технологически насыщенных объектов: фармацевтики, чистых помещений,
лабораторий, больниц и промышленности. Он принимает программу объекта,
размещает помещения и оборудование под жёсткими ограничениями, проверяет
потоки, зазоры и геометрию, затем выпускает editable DXF, vector PDF, IFC и
машиночитаемый audit trail.

Это не общий «AI генерирует планировки зданий». Ниша продукта — случаи, где
правдоподобной картинки недостаточно: нужны воспроизводимые координаты,
разделение людей/материалов/отходов, clearance оборудования и evidence по
каждой детерминированной проверке. Полная продуктовая формулировка и границы —
в [Facility Product Brief](docs/FACILITY_PRODUCT_BRIEF.md).

## Быстрый старт

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m layout_configurator.cli generate examples/basic.yaml --output out --variants 2
```

Результаты появятся в `out/`: `layout_01.dxf`, `layout_01.pdf`,
`layout_01.ifc`, JSON-снимок и `manifest.json`. Если в окружении уже есть обычный Python с `pip`, достаточно
заменить две первые команды на `python -m pip install -e .`. То же самое можно
запустить без установки entry point:

```powershell
python -m layout_configurator.cli generate examples/basic.yaml -o out
```

Типизированная правка существующего результата:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli edit out\layout_01.json `
  --move-room hall 100 0 --output edited
```

Доступны `--move-room ROOM DX DY`, `--resize-room ROOM WIDTH HEIGHT`,
`--add-door ROOM_A ROOM_B`, `--add-door-at ROOM_A ROOM_B OFFSET_MM WIDTH_MM`,
`--remove-door DOOR_ID`, `--add-window ROOM SIDE OFFSET_MM WIDTH_MM` и
`--remove-window WINDOW_ID`, `--set-external-entry ROOM SIDE OFFSET_MM WIDTH_MM`
и `--remove-external-entry`. Для `--add-door-at`, внешнего входа и ручного окна
`OFFSET_MM` —
центр проёма от нижнего/левого края соответствующей грани. После команды
изменённая геометрия фиксируется,
остальные комнаты частично пересчитываются CP-SAT и проходят повторную
проверку; при нарушении ограничений команда отклоняется, исходный JSON не
перезаписывается.

После правки можно сразу прогнать ruleset; результаты экспорта сохраняются даже
при нормативном FAIL, а код выхода `4` позволяет использовать команду в CI:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli edit out\layout_01.json `
  --move-room hall 100 0 --output edited `
  --rules rules\baseline.yaml
```

Проверка IFC по IDS-шаблону:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\layout_01.ifc --ids ids\layout_baseline.ids
```

Для KZ-проекции доступен отдельный IFC4 exchange-профиль (это контракт данных,
не нормативный verdict):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\kz_entry_pass\layout_01.ifc --ids ids\kz_layout_exchange.ids
```

Локальный браузерный редактор поверх тех же typed-команд:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui out\layout_01.json `
  --output out\ui --port 8765 `
  --rules rules\kz_sn_3_02_02_2023_partial.yaml --require-provenance
```

Откройте `http://127.0.0.1:8765/`. UI показывает SVG-проекцию текущего
`LayoutIR`, validation status и ссылки на переэкспортированные DXF/PDF/IFC/JSON;
сервер принимает только известные команды (`MoveRoom`, `ResizeRoom`, двери,
окна и внешний вход) и не принимает координаты как источник истины. Если передан
`--rules`, после каждого действия отображаются результаты deterministic ruleset.
На холсте drag комнаты и resize нижним правым маркером работают как preview;
при отпускании отправляется ровно одна `MoveRoom` или `ResizeRoom` с привязкой к
`grid_mm`, после чего сервер валидирует и переэкспортирует результат. Кнопки
undo/redo восстанавливают валидные снимки `EditorState`, а журнал показывает
последовательность typed-команд и их payload.

Ядро следует принципу solver-first: координаты выдаёт OR-Tools CP-SAT,
геометрия независимо проверяется валидатором, а DXF/PDF являются производными
представлениями `LayoutIR`. IFC/BIM покрывает структуру, стены, проёмы, связи
пространств, семантические типы и multi-storey projection; UI и LLM-граница
работают как отдельные слои согласно [актуальной product vision](docs/FACILITY_PRODUCT_BRIEF.md).

## Основной продуктовый слой: FacilityIR на базе BuildingIR

Первый узкий клин — фармацевтическое clean production / cleanroom-планирование.
Он хорошо проверяет ценность продукта: отдельные зоны, equipment clearance,
маршруты персонала и материалов, конструктивные оси и доказуемая проверка.
Лаборатории, больницы и промышленные профили подключаются отдельными
domain/rule packs поверх того же контракта, а не смешиваются в один набор
непроверяемых правил.

Текущий `BuildingIR` — канонический вход без координат: зоны, оборудование с
обслуживающими габаритами, направленные технологические потоки и
конструктивные оси. Пример clean-production программы находится в
[`examples/commercial_pilot.yaml`](examples/commercial_pilot.yaml), а целевой
план — в [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md).

`generate-building` уже решает комнаты CP-SAT, затем размещает оборудование
вторым CP-SAT с учётом clearance и выпускает `building_01.json`. Для плотных
facility-программ default time limit этой команды — 60 секунд:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\commercial_pilot.yaml --output out\commercial_pilot --variants 1
```

По умолчанию команда применяет YAML-backed compatibility-профиль
`rules/default_facility.yaml`. Для регулируемого объекта его нужно заменить
явным domain profile — например:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\commercial_pilot.yaml --profile rules\pharma_clean_production.yaml `
  --output out\commercial_pilot
```

В репозитории также есть domain rule packs
`rules\cleanroom_pilot.yaml`, `rules\laboratory_pilot.yaml`,
`rules\hospital_pilot.yaml` и `rules\industrial_pilot.yaml`. Они содержат
типизированные проверки потоков, а cleanroom-пакет также vocabulary/order классов
зон, роли и parent links airlock и pressure ordering с явно указанным sourced
guidance value. Pharma-пакет проверяет stage-flow types и обязательные derived
routes. Это project policies, а не GMP, healthcare, HSE, ISO или строительные
code-checks.
Каждый профиль может содержать секцию `drawing` (`sheet_id`, `discipline`,
`title`, `revision` и флаги аннотаций), которая управляет DXF/PDF projection.

Связи зон становятся жёсткими CP-SAT-ограничениями: обязательная связь требует
контакт хотя бы одной пары комнат, а запрещённая не допускает касания. Для
обязательных потоков эффективная ширина генерируемых дверей автоматически
поднимается до максимальной ширины потока; результат сохраняет это значение в
каноническом `spec` и evidence.

`generate-building` выпускает program/solver output и drawing-проекции: editable DXF
с блоками оборудования на `A-EQUIP` и пунктирными service-clearance на `A-CLEARANCE`,
а также векторный PDF. В `building_01.json` дополнительно сохраняются
`equipment_validation`, derived `flow_routes`, независимый `flow_validation` и
единый `facility_validation` с профилем, статусами и evidence. Необязательные
потоки (`required: false`) не делают результат FAIL при отсутствии маршрута;
если маршрут построен, он проходит те же геометрические проверки.
Рядом с ним команда сохраняет `building_01.coordination.json` — явный
`FLC-BCF-like-json` sidecar со стабильными issue ID, severity, source,
endpoint’ами и координатой маршрута, если проблема относится к flow. DXF/PDF
дополнительно содержат размеры помещений и подписи flow type/clear width.
Формат sidecar описан в [`schemas/coordination_issues.schema.json`](schemas/coordination_issues.schema.json);
дополнительно рядом автоматически создаётся настоящий BCF-XML 2.1 ZIP
`building_01.bcf`: по одному topic на issue, `markup.bcf`, viewpoint `*.bcfv`,
snapshot и внешние ссылки на program/DXF/PDF/IFC. JSON остаётся компактным
sidecar для автоматической обработки.
IFC для оборудования представлен как `IfcBuildingElementProxy` с двумя property
sets. Derived flow routes также экспортируются как `IfcBuildingElementProxy` с
`Curve3D` и `Pset_LayoutFlow`: тип, endpoint’ы, room path, ширина, число проблем
и статус независимой проверки. Это coordination/evidence projection, а не MEP
или process-system model, не GMP/медицинское разрешение и не автоматический
нормативный verdict.

После записи IFC автоматически открывается через IfcOpenShell обратно и сверяет
ключевые entity counts и flow metadata. В `manifest.json` это отражено в
`ifc_readback`; для ручной проверки уже существующего результата можно передать
`check-building --ifc path\to\building.ifc`.

Сохранённый результат можно перепроверить отдельным детерминированным запуском:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check-building `
  out\commercial_pilot\building_01.json --json
```

Для повторной проверки с отдельным issue-файлом добавьте
`--issues-output out\commercial_pilot\recheck.coordination.json`.
Для отдельного BCF-пакета при повторной проверке добавьте
`--bcf-output out\commercial_pilot\recheck.bcf`.
При следующей итерации можно передать предыдущий пакет через
`generate-building --bcf-input out\commercial_pilot\building_01.bcf`:
исчезнувшие из текущего validation report topics попадут в новый BCF как
`Closed`, а вернувшиеся ошибки снова будут `Open`.

Для полной проверки уже созданного комплекта используйте:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli qa-building `
  out\commercial_pilot\building_01.json --json
```

Команда сверяет JSON sidecar, DXF, PDF, IFC read-back и BCF 2.1 с текущим
facility validation report.

Для acceptance-проверки набора вариантов используйте:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\commercial_pilot.yaml --output out\commercial_pilot_acceptance `
  --variants 3 --time-limit 30 --seed 1 `
  --profile rules\pharma_clean_production.yaml
.venv\Scripts\python.exe -m layout_configurator.cli qa-building-set `
  out\commercial_pilot_acceptance --variants 3 `
  --profile rules\pharma_clean_production.yaml `
  --report out\commercial_pilot_acceptance\acceptance-report.json --json
```

`qa-building-set` запускает полный bundle QA для каждого `building_XX` и проверяет
совпадение semantic room/equipment/flow IDs, топологии маршрутов, IFC read-back
идентификаторов и BCF 2.1 topic identities между вариантами.
При указании `--report` дополнительно сохраняются SHA-256 и размеры каждого
required variant/shared-артефакта; optional issue-management sidecars также
отмечаются, если присутствуют.

Для read-only viewer можно передать весь acceptance-каталог и выбрать вариант:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\commercial_pilot_acceptance --variant 2 `
  --profile rules\pharma_clean_production.yaml
```

`--variant` выбирает `building_02.json` и его соседние DXF/PDF/IFC/BCF/JSON
артефакты; варианты 1 и 3 открываются тем же способом.

### Facility review и BCF issue history

Команда `ui` автоматически открывает существующий `building_01.json` как
read-only facility review. SVG показывает производные маршруты, service
clearance оборудования и автоматически подсвечивает текущие flow/equipment
конфликты. Боковая панель отображает статусы `OPEN`/`RESOLVED`, фильтр issues,
текущий список конфликтов и историю BCF topics; закрытые viewpoints отмечаются
на плане по координатам BCF. Для выбранного issue доступны `Resolve`, `Reopen`,
comment и assign; действия сохраняются в `building_01.issue-management.json`,
обновляют BCF/coordination JSON и manifest counters. Геометрия остаётся
read-only: edit/undo/redo/reset в review возвращают HTTP 405.

## Ограничения MVP

- один этаж и ортогональные прямоугольные комнаты;
- сетка координат по умолчанию 100 мм;
- контур — bounding box с прямоугольными вычитаемыми зонами;
- смежность означает общую границу не меньше ширины двери;
- стены экспортируются двойными линиями с толщиной и вырезами дверей;
- окна для комнат с `needs_daylight` экспортируются автоматически; ручные окна
  можно добавить через `edit --add-window ROOM SIDE OFFSET_MM WIDTH_MM`;
- solver требует для `needs_daylight` контакт комнаты с наружной гранью или
  вырезом, чтобы автоматическое окно не исчезало из результата;
- внешний вход задаётся через `external_entry`, фиксируется solver-ом на наружной
  стороне комнаты и экспортируется как отдельный `IfcDoor`/проём;
- `is_heated` хранится у комнаты и используется jurisdiction-проверками, но не
  заменяет расчёт отопления или инженерных систем; профиль KZ проверяет только
  полноту этой декларации по выбранным типам комнат;
- IFC содержит пространственную структуру, комнаты, непрерывные стены, двери,
  окна и `IfcOpeningElement` с `IfcRelVoidsElement`/`IfcRelFillsElement`;
  `IfcRelSpaceBoundary`, базовые property sets, материалы и IDS-шаблон уже есть;
  юрисдикционные
  нормативные проверки будут отдельным этапом;
- DXF/PDF — чертёжная выдача, не разрешение на строительство и не итоговая
  проверка строительных норм.

## Детерминированная проверка ruleset

Для layout JSON можно запустить версионируемый набор проектных правил:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\layout_01.json --rules rules\baseline.yaml
```

Для CI-проверки источника нормативного профиля добавляется
`--require-provenance`; он требует authority, edition, effective_date,
source_url и document_hash.

Первый частичный профиль Казахстана (проверенные срезы естественного освещения
по п. 7.8, запрещённого соседства по п. 6.2.13, тамбура по п. 6.2.8
и связи вспомогательных помещений по п. 6.2.12, а также декларации отопления
по п. 8.19
СН РК 3.02-02-2023):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
```

Для демонстрации проходящего профиля используйте пример, где кухня тоже
помечена как требующая естественного освещения:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_daylight.yaml --output out\kz_daylight --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_daylight\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
```

Проверка требования тамбура по п. 6.2.8 демонстрируется отдельными примерами:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_entry_pass.yaml --output out\kz_entry_pass --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_entry_pass\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance

.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_entry_fail.yaml --output out\kz_entry_fail --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_entry_fail\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
# Ожидаемый код выхода последней команды: 4.
```

`rules/baseline.yaml` проверяет геометрию, минимальные площади по типам комнат,
ширину коридора, окна для `needs_daylight`, достижимость от входа и максимальную
длину маршрута. Результат содержит `PASS`/`FAIL`/`NOT_APPLICABLE`, источник,
пункт ruleset и evidence по каждому правилу; `--json` выдаёт машинный отчёт.

Юрисдикционный профиль можно держать отдельным YAML и наследовать от baseline
через `extends: baseline.yaml`, переопределяя только нужные правила и пороги;
вложенные `params` объединяются с базовыми.
Для аудита профиль может хранить `provenance` с органом-источником, редакцией,
датой действия, URL и hash исходного документа; эти данные попадают в JSON-отчёт.
Профиль должен быть выбран и проверен человеком; без этого baseline остаётся
проектной самопроверкой, а не строительным кодом.

Это настраиваемый generic baseline для проектной самопроверки, а не универсальный
строительный код и не решение о разрешении на строительство. Юрисдикционные
профили должны добавляться отдельными ruleset-файлами после фиксации конкретной
юрисдикции. Числовые решения принимает код; LLM/RAG в verdict не участвуют.

## JSON Schema boundary

Канонический контракт `LayoutIR` описан в
[`schemas/layout_ir.schema.json`](schemas/layout_ir.schema.json). Проверка
нормализованного удобного YAML:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli schema examples\basic.yaml
```

Строгая проверка файла как есть, без нормализации и без молчаливого удаления
неизвестных полей:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli schema input.json --raw
```

`--raw` — граница будущего LLM-парсера. Парсер может выдавать только JSON по
схеме; координаты, DXF и нормативный verdict ему не выдаются.

Строгую нормализацию канонического JSON/YAML можно выполнить отдельно:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli normalize input.json `
  --output canonical.json
```

Для готового примера используй `examples/basic_canonical.json`; обычный
`examples/basic.yaml` остаётся shorthand для команды `generate`.

Для текстового ТЗ есть локальный constrained-parser без доступа к координатам:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli parse-brief `
  examples\brief.txt --output out\brief_canonical.json
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  out\brief_canonical.json --output out\brief --strict-input
```

Будущий LLM-провайдер должен отдавать только такой canonical JSON; функция
`parse_llm_mapping` сначала прогоняет его через JSON Schema и отклоняет
координаты, generated data и неизвестные поля. Для RAG-слоя есть безопасный
поиск ссылок без проверки планировки:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli cite `
  естественное освещение кухни --rules rules\kz_sn_3_02_02_2023_partial.yaml
```

При наличии внешнего OpenAI-compatible провайдера можно включить реальный
LLM-вызов без передачи ему результата солвера или нормативного verdict:

```powershell
$env:LAYOUT_LLM_ENDPOINT = "https://provider.example/v1/chat/completions"
$env:LAYOUT_LLM_MODEL = "your-model"
$env:LAYOUT_LLM_API_KEY = "your-key"
.venv\Scripts\python.exe -m layout_configurator.cli parse-llm `
  examples\brief.txt --output out\llm_canonical.json
```

Ответ провайдера принимается только после `parse_llm_mapping` и JSON Schema.

Для двух и более уровней используется общая осевая координация: повторяющиеся
комнаты вертикального ядра фиксируются на одной прямоугольной сетке, а ширина
лестницы и заявленные оси проверяются после решения:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-multifloor `
  examples\multifloor.yaml --output out\multifloor
```

Команда создаёт также общий `out\multifloor\multifloor.ifc` с несколькими
`IfcBuildingStorey` и `IfcStair`; отдельные папки этажей сохраняются для
локального редактирования и round-trip.

Команда сначала проверяет схему, затем создаёт `LayoutIR`; результат солвера
и координаты на этом input contract boundary не принимаются.

Для прямого запуска солвера с этим же строгим входом используй
`generate --strict-input`; shorthand без обязательных canonical-полей будет
отклонён до запуска CP-SAT.
### Facility review UI

Opening a generated `BuildingIR` result automatically selects the read-only facility review:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\commercial_pilot\building_01.json --profile rules\pharma_clean_production.yaml
```

The review projects rooms, process equipment, service-clearance envelopes, derived people/material/waste routes, structural axes, deterministic facility evidence, coordination issues, and the generated JSON/DXF/PDF/IFC/BCF artifacts. It recomputes the facility report on open and rejects edit, undo, redo, and reset commands with HTTP 405.
