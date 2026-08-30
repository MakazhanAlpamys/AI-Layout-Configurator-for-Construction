# AI Layout Configurator for Construction

Детерминированный генератор одноэтажных прямоугольных планировок. На вход
принимает YAML/JSON с габаритами здания, комнатами, площадями и смежностями;
на выходе создаёт варианты планировок в DXF и векторном PDF.

## Быстрый старт

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m layout_configurator.cli generate examples/basic.yaml --output out --variants 2
```

Результаты появятся в `out/`: `layout_01.dxf`, `layout_01.pdf`, JSON-снимок
и `manifest.json`. Если в окружении уже есть обычный Python с `pip`, достаточно
заменить две первые команды на `python -m pip install -e .`. То же самое можно
запустить без установки entry point:

```powershell
python -m layout_configurator.cli generate examples/basic.yaml -o out
```

Ядро следует принципу solver-first: координаты выдаёт OR-Tools CP-SAT,
геометрия независимо проверяется валидатором, а DXF/PDF являются производными
представлениями `LayoutIR`. LLM, IFC/BIM и веб-редактор пока не реализованы;
они идут после стабилизации ядра согласно [дорожной карте](docs/ROADMAP.md).

## Ограничения MVP

- один этаж и ортогональные прямоугольные комнаты;
- сетка координат по умолчанию 100 мм;
- контур — bounding box с прямоугольными вычитаемыми зонами;
- смежность означает общую границу не меньше ширины двери;
- DXF/PDF — чертёжная выдача, не разрешение на строительство и не итоговая
  проверка строительных норм.
