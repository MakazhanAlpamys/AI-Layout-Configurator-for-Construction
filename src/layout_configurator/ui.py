"""Small local web editor over the typed command API.

The browser is deliberately a thin client: it renders the current LayoutIR
projection and sends command payloads to :class:`EditorState`. It never moves
rooms or writes CAD files by itself.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .commands import (
    AddDoor,
    AddWindow,
    EditError,
    MoveRoom,
    RemoveDoor,
    RemoveExternalEntry,
    RemoveWindow,
    ResizeRoom,
    SetExternalEntry,
)
from .editor import EditorState
from .export import export_bundle
from .ifc import export_ifc
from .io import load_result, load_spec, write_result
from .models import LayoutIR, LayoutResult
from .norms import RuleSet, check_layout, load_ruleset
from .solver import InfeasibleLayout, solve_layouts
from .validation import validate_layout
from .walls import build_wall_plan


ASSET_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


def _asset(name: str) -> str:
    return files("layout_configurator").joinpath("static", name).read_text(encoding="utf-8")


@dataclass
class UiSession:
    """Mutable server-side editing session; the LayoutIR remains authoritative."""

    initial_spec: LayoutIR
    initial_result: LayoutResult
    output_dir: Path
    state: EditorState
    lock: threading.RLock
    ruleset: RuleSet | None = None
    past: list[EditorState] = field(default_factory=list)
    future: list[EditorState] = field(default_factory=list)
    journal: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_input(
        cls,
        input_path: str | Path,
        output_dir: str | Path,
        *,
        solve_time_limit_seconds: float = 15,
        seed: int = 42,
        rules_path: str | Path | None = None,
        require_provenance: bool = False,
    ) -> "UiSession":
        if require_provenance and not rules_path:
            raise ValueError("--require-provenance requires --rules")
        source = Path(input_path)
        try:
            spec, result = load_result(source)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            spec = load_spec(source)
            result = solve_layouts(
                spec,
                variants=1,
                time_limit_seconds=solve_time_limit_seconds,
                seed=seed,
            )[0]
        state = EditorState.from_layout(spec, result)
        ruleset = load_ruleset(rules_path) if rules_path else None
        if ruleset and require_provenance:
            issues = ruleset.provenance_issues()
            if issues:
                raise ValueError("ruleset provenance is incomplete: " + "; ".join(issues))
        session = cls(
            initial_spec=spec,
            initial_result=result,
            output_dir=Path(output_dir),
            state=state,
            lock=threading.RLock(),
            ruleset=ruleset,
        )
        session._export()
        return session

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            wall_plan = build_wall_plan(self.state.spec, self.state.result)
            rooms = []
            for room in self.state.spec.rooms:
                rect = self.state.result.placements[room.id]
                rooms.append(
                    {
                        "id": room.id,
                        "type": room.type,
                        "target_area_m2": room.target_area_m2,
                        "needs_daylight": room.needs_daylight,
                        "is_heated": room.is_heated,
                        "rect": {
                            "x": rect.x,
                            "y": rect.y,
                            "width": rect.width,
                            "height": rect.height,
                            "area_m2": rect.area_m2,
                        },
                    }
                )
            norms = check_layout(self.state.spec, self.state.result, self.ruleset) if self.ruleset else None
            return {
                "spec": self.state.spec.to_dict(),
                "layout": self.state.result.to_dict(),
                "boundary": {
                    "width": self.state.spec.boundary.width_mm,
                    "height": self.state.spec.boundary.height_mm,
                },
                "rooms": rooms,
                "openings": [_opening_to_dict(opening) for opening in wall_plan.openings],
                "windows": [_opening_to_dict(window) for window in wall_plan.windows],
                "validation": {
                    "ok": self.state.report.ok,
                    "issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in self.state.report.issues
                    ],
                },
                "norms": norms.to_dict() if norms else None,
                "history": list(self.state.history),
                "journal": list(self.journal),
                "can_undo": bool(self.past),
                "can_redo": bool(self.future),
                "files": [
                    {"name": name, "url": f"/files/{name}"}
                    for name in self._file_names()
                ],
            }

    def apply_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = command_from_payload(payload)
        with self.lock:
            new_state = self.state.apply(command)
            self.past.append(self.state)
            self.future.clear()
            self.state = new_state
            self.journal.append(
                {
                    "action": "command",
                    "type": type(command).__name__,
                    "payload": dict(payload),
                }
            )
            self._export()
            return self.snapshot()

    def undo(self) -> dict[str, Any]:
        with self.lock:
            if not self.past:
                raise EditError("Нет изменений для отмены")
            self.future.append(self.state)
            self.state = self.past.pop()
            self.journal.append({"action": "undo", "type": "Undo"})
            self._export()
            return self.snapshot()

    def redo(self) -> dict[str, Any]:
        with self.lock:
            if not self.future:
                raise EditError("Нет изменений для повтора")
            self.past.append(self.state)
            self.state = self.future.pop()
            self.journal.append({"action": "redo", "type": "Redo"})
            self._export()
            return self.snapshot()

    def reset(self) -> dict[str, Any]:
        with self.lock:
            self.state = EditorState.from_layout(self.initial_spec, self.initial_result)
            self.past.clear()
            self.future.clear()
            self.journal.clear()
            self._export()
            return self.snapshot()

    def file_path(self, name: str) -> Path | None:
        safe_name = Path(unquote(name)).name
        if safe_name != name or safe_name not in self._file_names():
            return None
        path = (self.output_dir / safe_name).resolve()
        if path.parent != self.output_dir.resolve() or not path.is_file():
            return None
        return path

    def _export(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        export_bundle(self.output_dir, self.state.spec, self.state.result, self.state.report)
        export_ifc(self.output_dir / f"layout_{self.state.result.variant:02d}.ifc", self.state.spec, self.state.result)
        write_result(self.output_dir / f"layout_{self.state.result.variant:02d}.json", self.state.spec, self.state.result)

    def _file_names(self) -> tuple[str, ...]:
        stem = f"layout_{self.state.result.variant:02d}"
        return tuple(f"{stem}{suffix}" for suffix in (".dxf", ".pdf", ".ifc", ".json"))


def create_ui_server(session: UiSession, address: tuple[str, int] = ("127.0.0.1", 0)) -> ThreadingHTTPServer:
    """Create a testable HTTP server for one editing session."""

    class Handler(_UiHandler):
        current_session = session

    return ThreadingHTTPServer(address, Handler)


def serve_ui(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    rules_path: str | Path | None = None,
    require_provenance: bool = False,
) -> int:
    session = UiSession.from_input(
        input_path,
        output_dir,
        rules_path=rules_path,
        require_provenance=require_provenance,
    )
    server = create_ui_server(session, (host, port))
    print(f"UI editor: http://{host}:{server.server_port}/")
    print(f"exports: {session.output_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("UI editor stopped")
    finally:
        server.server_close()
    return 0


class _UiHandler(BaseHTTPRequestHandler):
    current_session: UiSession

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        route = urlsplit(self.path).path
        if route == "/" or route == "/index.html":
            self._send_text(200, _asset("index.html"), ASSET_TYPES["index.html"])
            return
        if route in {"/app.js", "/style.css"}:
            name = route[1:]
            self._send_text(200, _asset(name), ASSET_TYPES[name])
            return
        if route == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if route == "/api/state":
            self._send_json(200, self.current_session.snapshot())
            return
        if route.startswith("/files/"):
            path = self.current_session.file_path(route.removeprefix("/files/"))
            if path is None:
                self._send_json(404, {"error": "file not found"})
                return
            content_type = {
                ".dxf": "application/dxf",
                ".pdf": "application/pdf",
                ".ifc": "application/x-step",
                ".json": "application/json; charset=utf-8",
            }[path.suffix.lower()]
            self._send_bytes(200, path.read_bytes(), content_type)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        route = urlsplit(self.path).path
        if route not in {"/api/command", "/api/reset", "/api/undo", "/api/redo"}:
            self._send_json(404, {"error": "not found"})
            return
        try:
            if route == "/api/command":
                snapshot = self.current_session.apply_payload(self._read_json())
            elif route == "/api/undo":
                snapshot = self.current_session.undo()
            elif route == "/api/redo":
                snapshot = self.current_session.redo()
            else:
                snapshot = self.current_session.reset()
        except (EditError, KeyError, TypeError, ValueError, InfeasibleLayout, RuntimeError) as exc:
            self._send_json(400, {"error": str(exc), "state": self.current_session.snapshot()})
            return
        self._send_json(200, snapshot)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length <= 0 or length > 1_000_000:
            raise ValueError("request body must be between 1 byte and 1 MB")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("command payload must be an object")
        return payload

    def _send_json(self, status: int, payload: object) -> None:
        self._send_text(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _send_text(self, status: int, content: str, content_type: str) -> None:
        self._send_bytes(status, content.encode("utf-8"), content_type)

    def _send_bytes(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def command_from_payload(payload: dict[str, Any]):
    """Convert one UI JSON action into an existing typed command."""

    action = str(payload.get("type", "")).strip().lower()
    if action == "move_room":
        return MoveRoom(str(payload.get("room_id", "")), _number(payload, "dx_mm"), _number(payload, "dy_mm"))
    if action == "resize_room":
        return ResizeRoom(
            str(payload.get("room_id", "")),
            _number(payload, "width_mm"),
            _number(payload, "height_mm"),
            str(payload.get("anchor", "center")),
        )
    if action == "add_door":
        offset = payload.get("offset_mm")
        width = payload.get("width_mm")
        if (offset in (None, "")) != (width in (None, "")):
            raise EditError("Door offset and width must be supplied together")
        return AddDoor(
            str(payload.get("room_a", "")),
            str(payload.get("room_b", "")),
            None if offset in (None, "") else float(offset),
            None if width in (None, "") else float(width),
            str(payload.get("door_id", "")),
        )
    if action == "remove_door":
        return RemoveDoor(str(payload.get("door_id", "")))
    if action == "add_window":
        return AddWindow(
            str(payload.get("room_id", "")),
            str(payload.get("side", "")),
            _number(payload, "offset_mm"),
            _number(payload, "width_mm"),
            str(payload.get("window_id", "")),
        )
    if action == "remove_window":
        return RemoveWindow(str(payload.get("window_id", "")))
    if action == "set_external_entry":
        return SetExternalEntry(
            str(payload.get("room_id", "")),
            str(payload.get("side", "")),
            _number(payload, "offset_mm"),
            _number(payload, "width_mm"),
            str(payload.get("entry_id", "")),
        )
    if action == "remove_external_entry":
        return RemoveExternalEntry()
    raise EditError(f"Unknown UI command type: {action or '<empty>'}")


def _number(payload: dict[str, Any], key: str) -> float:
    try:
        return float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise EditError(f"{key} must be a number") from exc


def _opening_to_dict(opening: object) -> dict[str, Any]:
    result = {
        "orientation": opening.orientation,
        "fixed": opening.fixed,
        "start": opening.start,
        "end": opening.end,
        "width": opening.width,
        "center": opening.center,
        "id": opening.id,
    }
    if hasattr(opening, "room_a"):
        result.update({"room_a": opening.room_a, "room_b": opening.room_b, "external": opening.external})
    else:
        result["room_id"] = opening.room_id
    return result
