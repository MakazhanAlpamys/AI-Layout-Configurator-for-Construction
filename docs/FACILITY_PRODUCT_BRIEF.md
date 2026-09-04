# Facility Layout Compiler: продуктовая цель

## Решение

Проект больше не позиционируется как общий «AI генерирует планировки зданий».
Его цель — **solver-first компилятор проверяемых планировок для регулируемых и
технологически насыщенных объектов**:

- фармацевтических производств и cleanroom-участков;
- лабораторий;
- больничных и медицинских функциональных блоков;
- промышленных и складско-производственных объектов.

Название продукта: **Facility Layout Compiler**. `BuildingIR` остаётся текущим
каноническим контрактом в коде; `FacilityIR` — направление его развития, а не
переименование ради переименования.

## Почему этот продукт отличается

Общий генератор планировок конкурирует по скорости картинки, стилю и количеству
вариантов. Для целевой ниши этого недостаточно. Решение имеет ценность, только
если можно проверить и объяснить:

1. где размещено оборудование и достаточно ли service clearance;
2. проходят ли люди, сырьё, продукт, отходы и сервис по разрешённым маршрутам;
3. разделены ли требуемые зоны и какие связи между ними обязательны;
4. какие правила проверены, с какой версией и каким evidence;
5. как один и тот же результат попадает в editable DXF, vector PDF, IFC и JSON.

Поэтому LLM может помочь извлечь программу из ТЗ или найти ссылку на правило,
но не производит координаты, DXF/IFC или нормативный verdict. Геометрия и
проверки остаются детерминированными.

## Первый клин

Первый профиль — **pharma-like clean production / cleanroom planning**. Он
достаточно узок, чтобы валидировать модель с технологом и архитектором, и
достаточно сложен, чтобы отличить продукт от бытового floor-planner:

- зоны receiving, raw storage, preparation, clean production, packaging,
  finished storage, dispatch и staff;
- equipment footprints, fixed wall anchors и обслуживающие зоны;
- потоки персонала, материалов, готовой продукции, отходов и сервиса;
- проходы и дверные проёмы с минимальной шириной;
- конструктивные оси, помещения, стены, проёмы и выходные чертежи.

Это **не** утверждение GMP compliance, классификации чистоты помещения или
права выпускать проект. Такие statements появляются только с подтверждённым
профилем правил, их версией и экспертной проверкой.

## Пользователи и итог работы

| Роль | Что получает |
|---|---|
| Технолог / facility planner | Варианты layout с оборудованием и потоками вместо ручного перебора. |
| Архитектор / BIM-координатор | Редактируемые DXF и IFC, а не raster-картинку. |
| QA / validation / project manager | Версионируемый JSON evidence: ограничения, PASS/FAIL/UNKNOWN и стабильный seed. |
| Инженер заказчика | Понятные границы автоматизации и список условий, которые требует эксперт. |

## Контракт результата

```text
ТЗ / существующий DXF / табличная программа
                    ↓
canonical BuildingIR → будущие facility domain packs
                    ↓
CP-SAT: помещения → оборудование → маршруты
                    ↓
independent geometry and rule validation
                    ↓
DXF + vector PDF + IFC + JSON evidence bundle
```

Каждая стрелка сохраняет идентификаторы и не превращает производный артефакт в
источник координат. Изменения идут через typed commands и повторную проверку.

## Состояние на сегодня

Уже реализовано для single-floor pilot:

- `BuildingIR`: зоны, equipment, flows и structural grid без входных координат;
- CP-SAT раскладка помещений и отдельный CP-SAT packing оборудования;
- hard zone adjacency groups for required/forbidden functional relations;
- service clearance, повороты, wall anchors и `NoOverlap2D`;
- независимый equipment/flow/facility validator и JSON audit evidence;
- automatic effective door width promotion for required process flows and
  equipment front-edge access points for routing;
- derived IFC flow route proxies with route metadata and validation status;
- stable BCF-like JSON sidecars plus BCF-XML 2.1 ZIP topics/viewpoints;
- IFC read-back of entity counts and route metadata before a bundle is accepted;
- readable drawing annotations for room sizes, flow types and clear widths;
- YAML-driven sheet metadata and annotation toggles for DXF/PDF;
- editable DXF, vector PDF и IFC equipment proxies;
- конструктивные оси в DXF/PDF;
- versioned YAML rule packs for `pharma`, `cleanroom`, `laboratory`, `hospital`
  and `industrial` domains;
- `check-building` round-trip validation command;
- пример `examples/commercial_pilot.yaml`, теперь обозначенный как
  pharmaceutical clean-production pilot with explicit process stages and a
  waste branch; cold-store placement preserves a service aisle for the waste
  route, and the generated pilot now passes the deterministic facility/bundle
  QA checks.

Это ещё не готовая фармацевтическая или медицинская система. Текущие комнаты
прямоугольные, flow routing ограничен 2D pilot-логикой, а rule packs не заменяют
нормативный экспертный review.

## Auditable domain rule packs

Все facility-правила хранятся в YAML-профилях. Каждое правило обязано иметь
`id`, `kind`, `source`, `edition`, `effective_date`, `evidence` и `parameters`;
результат проверки сохраняет ту же provenance-связь в `facility_validation` и
BCF-like issues.

В репозитории зафиксированы отдельные пакеты:

- `rules/cleanroom_pilot.yaml` — классы зон, pressure ordering, airlock и
  clean/dirty flow separation;
- `rules/pharma_clean_production.yaml` — material, personnel, finished goods и
  waste declarations/separation;
- `rules/laboratory_pilot.yaml` — specimen, personnel, clean supply и waste;
- `rules/hospital_pilot.yaml` — patient, personnel, clean supply, dirty supply
  и waste;
- `rules/industrial_pilot.yaml` — material, personnel, vehicles, hazardous
  materials, maintenance и waste.

`schemas/facility_profile.schema.json` проверяет структуру пакета. Отсутствующее
входное доказательство даёт `UNKNOWN`, а не выдуманный PASS. Нормативные числа
не добавляются по умолчанию: числовой параметр допускается только как явно
заданный параметр конкретного проектного профиля с собственной ссылкой и
evidence. Все профили прямо помечены как project policy, а не regulatory verdict.

## Продуктовые профили — не одна универсальная база норм

Профиль состоит из трёх независимых частей:

1. **Domain vocabulary** — типы зон, помещений, оборудования и потоков.
2. **Deterministic geometry checks** — clearance, separation, маршруты,
   проёмы, эвакуация и другие подтверждённые ограничения.
3. **Data/IFC exchange contract** — обязательные свойства и IDS-проверки.

Pharma/cleanroom, laboratory, hospital и industrial profiles версионируются
раздельно. Неприменимое правило возвращает `NOT_APPLICABLE`, а неизвестное
условие — `UNKNOWN`; система не маскирует их как PASS.

## Ближайшая последовательность

1. **Domain profile pack.** Провести expert review controlled vocabulary,
   allowed/forbidden flow relations и evidence каждого правила. YAML-пакеты для
   pharma, cleanroom, laboratory, hospital и industrial уже реализованы; до
   такого review они остаются project policies, а не нормативными verdicts.
2. **Coordination exchange.** Повторное чтение IFC flow proxies, BCF-XML 2.1
   topics/viewpoints и read-only viewer QA: конфликтные маршруты/оборудование
   подсвечиваются, `OPEN`/`RESOLVED` фильтруются, а BCF history видна на плане.
3. **Confirmed domain packs.** Расширять YAML-профили отдельными spec →
   implementation → review циклами только вместе с domain experts и
   подтверждёнными источниками норм.

## Что не обещаем

- нейросеть, которая рисует финальный чертёж;
- «полное соответствие GMP», hospital code или любой другой норме без
  конкретного подтверждённого profile и экспертного sign-off;
- универсальную базу норм всех стран;
- выпуск разрешения на строительство;
- полноценный MEP, process engineering или structural calculation.

Именно эти границы позволяют делать продукт проверяемым, а не просто убедительно
выглядящим.
