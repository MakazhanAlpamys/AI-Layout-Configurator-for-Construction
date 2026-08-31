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
`--remove-window WINDOW_ID`. Для `--add-door-at` и ручного окна `OFFSET_MM` —
центр проёма от нижнего/левого края соответствующей грани. После команды
изменённая геометрия фиксируется,
остальные комнаты частично пересчитываются CP-SAT и проходят повторную
проверку; при нарушении ограничений команда отклоняется, исходный JSON не
перезаписывается.

Проверка IFC по IDS-шаблону:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\layout_01.ifc --ids ids\layout_baseline.ids
```

Ядро следует принципу solver-first: координаты выдаёт OR-Tools CP-SAT,
геометрия независимо проверяется валидатором, а DXF/PDF являются производными
представлениями `LayoutIR`. IFC/BIM уже покрывает базовую структуру, стены,
проёмы, связи пространств и семантические типы; веб-редактор и LLM-парсер
остаются следующими этапами согласно [дорожной карте](docs/ROADMAP.md).

## Ограничения MVP

- один этаж и ортогональные прямоугольные комнаты;
- сетка координат по умолчанию 100 мм;
- контур — bounding box с прямоугольными вычитаемыми зонами;
- смежность означает общую границу не меньше ширины двери;
- стены экспортируются двойными линиями с толщиной и вырезами дверей;
- окна для комнат с `needs_daylight` экспортируются автоматически; ручные окна
  можно добавить через `edit --add-window ROOM SIDE OFFSET_MM WIDTH_MM`;
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

Команда сначала проверяет схему, затем создаёт `LayoutIR`; результат солвера
и координаты на этом input contract boundary не принимаются.

Для прямого запуска солвера с этим же строгим входом используй
`generate --strict-input`; shorthand без обязательных canonical-полей будет
отклонён до запуска CP-SAT.
