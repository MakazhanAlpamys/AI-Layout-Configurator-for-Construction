"""Concrete paths to bundled schemas, rules and IDS in source or wheel installs."""

from pathlib import Path


_PACKAGE_DATA = Path(__file__).resolve().parent / "_data"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_CATEGORIES = frozenset({"schemas", "rules", "ids"})


def resource_path(relative: str | Path) -> Path:
    """Find a built-in asset without depending on the working directory.

    Wheels contain generated copies under ``_data``. Editable/source installs
    use the same authoritative files at the project root.
    """
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] not in _CATEGORIES:
        raise ValueError(f"Not a bundled resource path: {relative}")
    packaged = _PACKAGE_DATA / name
    if packaged.is_file():
        return packaged
    source = _SOURCE_ROOT / name
    if (_SOURCE_ROOT / "pyproject.toml").is_file() and source.is_file():
        return source
    raise FileNotFoundError(f"Bundled resource is missing: {relative}")


def resolve_resource_path(path: str | Path) -> Path:
    """Preserve supplied files; resolve absent built-in relative defaults.

    Absolute paths and existing local overrides retain their meaning. Only
    schema/rule/IDS paths can fall back to bundled resources.
    """
    selected = Path(path)
    if selected.is_absolute() or selected.is_file():
        return selected
    if selected.parts and selected.parts[0] in _CATEGORIES and ".." not in selected.parts:
        return resource_path(selected)
    return selected
