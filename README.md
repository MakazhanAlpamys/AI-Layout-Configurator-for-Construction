# AI Layout Configurator for Construction

Детерминированный генератор одноэтажных прямоугольных планировок. На вход
принимает YAML/JSON с габаритами здания, комнатами, площадями и смежностями;
на выходе создаёт варианты планировок в DXF, векторном PDF и базовом IFC4.

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
работают как отдельные слои согласно [дорожной карте](docs/ROADMAP.md).

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
