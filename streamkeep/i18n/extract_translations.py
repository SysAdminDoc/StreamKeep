#!/usr/bin/env python3
"""Extract hand-authored Qt UI strings into deterministic TS catalogs.

PyQt's ``pylupdate`` only sees explicit ``tr()`` calls.  StreamKeep's UI is
hand-authored, so this extractor also recognizes the widget constructors and
setter/helper calls that own visible text.  It is the lupdate-equivalent step
used before lrelease compiles the checked-in catalogs.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = Path(__file__).parent
SOURCE_DIRS = (ROOT / "streamkeep" / "ui", ROOT / "streamkeep" / "player")

FIRST_ARG_CALLS = {
    "QLabel", "QPushButton", "QCheckBox", "QGroupBox", "QRadioButton",
    "setText", "setPlaceholderText", "setToolTip", "setStatusTip",
    "setWhatsThis", "setWindowTitle", "setAccessibleName",
    "setAccessibleDescription", "addAction", "insertAction", "addItem",
    "insertItem", "_set_status", "_field_label",
}
ALL_ARG_CALLS = {
    "make_dialog_hero", "make_dialog_section", "make_empty_state",
    "make_field_block", "make_metric_card", "update_status_banner",
}
LIST_ARG_CALLS = {"addItems", "setHorizontalHeaderLabels", "setHeaderLabels"}


@dataclass(frozen=True)
class Message:
    context: str
    source: str
    numerus: bool = False


SPANISH_CORE = {
    # Shell and navigation.
    ("StreamKeep", "Download"): "Descargar",
    ("StreamKeep", "Monitor"): "Monitor",
    ("StreamKeep", "History"): "Historial",
    ("StreamKeep", "Storage"): "Almacenamiento",
    ("StreamKeep", "Analytics"): "Analíticas",
    ("StreamKeep", "Settings"): "Configuración",
    ("StreamKeep", "Search downloads, URLs, channels, or podcasts…"):
        "Buscar descargas, URL, canales o pódcasts…",
    ("StreamKeep", "Recent notifications"): "Notificaciones recientes",
    ("StreamKeep", "Alerts 0"): "Alertas 0",
    ("StreamKeep", "Stop"): "Detener",
    ("StreamKeep", "Open Folder"): "Abrir carpeta",
    ("StreamKeep", "Trim..."): "Recortar...",
    # URL and download workflow.
    ("StreamKeep", "New download"): "Nueva descarga",
    ("StreamKeep", "Source detected"): "Origen detectado",
    ("StreamKeep", "Paste a stream, VOD, podcast, or media URL."):
        "Pegue una URL de emisión, VOD, pódcast o archivo multimedia.",
    ("StreamKeep", "Source URL"): "URL de origen",
    ("StreamKeep", "Paste a stream, channel, VOD, or direct media URL…"):
        "Pegue una URL de emisión, canal, VOD o archivo multimedia…",
    ("StreamKeep", "Fetch"): "Obtener",
    ("StreamKeep", "Fetching"): "Obteniendo",
    ("StreamKeep", "Import URLs"): "Importar URL",
    ("StreamKeep", "Paste"): "Pegar",
    ("StreamKeep", "Scan page"): "Escanear página",
    ("StreamKeep", "Allow LAN for this scan"): "Permitir LAN para este análisis",
    ("StreamKeep", "Download Selected"): "Descargar selección",
    ("StreamKeep", "Download All Checked"): "Descargar todos los marcados",
    ("StreamKeep", "Quality:"): "Calidad:",
    ("StreamKeep", "Output:"): "Salida:",
    ("StreamKeep", "Browse"): "Examinar",
    ("StreamKeep", "Browse…"): "Examinar…",
    ("StreamKeep", "Resume"): "Reanudar",
    ("StreamKeep", "Discard"): "Descartar",
    ("StreamKeep", "Remove"): "Eliminar",
    ("StreamKeep", "Clear"): "Borrar",
    ("StreamKeep", "Close"): "Cerrar",
    ("StreamKeep", "Cancel"): "Cancelar",
    ("StreamKeep", "Save"): "Guardar",
    # History workflow.
    ("StreamKeep", "Download History"): "Historial de descargas",
    ("StreamKeep", "Clear History"): "Borrar historial",
    ("StreamKeep", "Downloads"): "Descargas",
    ("StreamKeep", "Latest"): "Más reciente",
    ("StreamKeep", "Top Platform"): "Plataforma principal",
    ("StreamKeep", "Top Channel"): "Canal principal",
    ("StreamKeep", "Preview"): "Vista previa",
    ("StreamKeep", "Date"): "Fecha",
    ("StreamKeep", "Platform"): "Plataforma",
    ("StreamKeep", "Channel"): "Canal",
    ("StreamKeep", "Title"): "Título",
    ("StreamKeep", "Quality"): "Calidad",
    ("StreamKeep", "Files"): "Archivos",
    ("StreamKeep", "Size"): "Tamaño",
    ("StreamKeep", "Path"): "Ruta",
    ("StreamKeep", "Missing"): "No disponible",
    ("StreamKeep", "Loading"): "Cargando",
    ("StreamKeep", "No entries"): "Sin entradas",
    ("StreamKeep", "Completed downloads appear here"):
        "Las descargas completadas aparecen aquí",
    ("StreamKeep", "Find a Download"): "Buscar una descarga",
    ("StreamKeep", "Showing all downloads"): "Mostrando todas las descargas",
    ("StreamKeep", "Search title, platform, channel, path, or URL…"):
        "Buscar título, plataforma, canal, ruta o URL…",
    ("StreamKeep", "Search Transcript Text"): "Buscar texto de transcripción",
    ("StreamKeep", "Download history builds automatically after each completed job."):
        "El historial se crea automáticamente después de cada tarea completada.",
    ("StreamKeep", "History fills in automatically"):
        "El historial se completa automáticamente",
    ("StreamKeep", "No downloads matched that search. Try a broader title, platform, channel, or folder term."):
        "Ninguna descarga coincide. Pruebe un título, plataforma, canal o carpeta más general.",
    # Settings workflow.
    ("StreamKeep", "Appearance"): "Apariencia",
    ("StreamKeep", "Language"): "Idioma",
    ("StreamKeep", "English"): "Inglés",
    ("StreamKeep", "Español"): "Español",
    ("StreamKeep", "Dark (Catppuccin Mocha)"): "Oscuro (Catppuccin Mocha)",
    ("StreamKeep", "Light (Catppuccin Latte)"): "Claro (Catppuccin Latte)",
    ("StreamKeep", "System"): "Sistema",
    ("StreamKeep", "Default Output"): "Salida predeterminada",
    ("StreamKeep", "Local Toolchain"): "Herramientas locales",
    ("StreamKeep", "Save Settings"): "Guardar configuración",
    ("StreamKeep", "Settings saved and applied to future downloads."):
        "La configuración se guardó y se aplicará a futuras descargas.",
    # Common dialogs and errors.
    ("StreamKeep", "Search"): "Buscar",
    ("StreamKeep", "All"): "Todo",
    ("StreamKeep", "Info"): "Información",
    ("StreamKeep", "Success"): "Correcto",
    ("StreamKeep", "Warning"): "Advertencia",
    ("StreamKeep", "Error"): "Error",
    ("StreamKeep", "Notification Log"): "Registro de notificaciones",
    ("StreamKeep", "Notification history"): "Historial de notificaciones",
    ("StreamKeep", "Filters"): "Filtros",
    ("StreamKeep", "Results"): "Resultados",
    ("StreamKeep", "Level"): "Nivel",
    ("StreamKeep", "Time"): "Hora",
    ("StreamKeep", "Message"): "Mensaje",
    ("StreamKeep", "Save profile"): "Guardar perfil",
    ("StreamKeep", "Already Downloaded"): "Ya descargado",
    ("StreamKeep", "Download again?"): "¿Descargar de nuevo?",
    ("StreamKeep", "Remove from History"): "Eliminar del historial",
    # Explicit dynamic contexts.
    ("Status", "Ready"): "Listo",
    ("Status", "Working"): "En curso",
    ("Status", "Finalizing"): "Finalizando",
    ("Status", "Attention"): "Atención",
    ("Status", "Error"): "Error",
    ("Status", "Paste a URL to begin."): "Pegue una URL para comenzar.",
    ("Status", "Fetching stream info and available playback options..."):
        "Obteniendo información y opciones de reproducción...",
    ("Status", "Language updated across StreamKeep."):
        "El idioma se actualizó en StreamKeep.",
    ("Status", "Language file could not be loaded."):
        "No se pudo cargar el archivo de idioma.",
    ("Status", "The download could not be started."):
        "No se pudo iniciar la descarga.",
    ("Status", "Paste a URL first."): "Pegue primero una URL.",
    ("Status", "Scan Page expects a full http(s) URL."):
        "Escanear página requiere una URL http(s) completa.",
    ("Status", "Integrity manifest rescan failed."):
        "No se pudo volver a analizar el manifiesto de integridad.",
    ("Status", "Path missing and no saved URL to retry."):
        "Falta la ruta y no hay una URL guardada para reintentar.",
    ("Status", "Settings not saved: secure credential storage unavailable."):
        "No se guardó la configuración: el almacén seguro no está disponible.",
    ("Status", "Settings saved and applied to future downloads."):
        "La configuración se guardó y se aplicará a futuras descargas.",
    ("Accessibility", "Application state: {state}"):
        "Estado de la aplicación: {state}",
    ("History", "%n saved download(s)"): (
        "%n descarga guardada", "%n descargas guardadas"
    ),
    ("History", "%n download(s)"): ("%n descarga", "%n descargas"),
    ("History", "Showing all %n download(s)"): (
        "Mostrando %n descarga", "Mostrando las %n descargas"
    ),
    ("History", "{platform_count} / {total} downloads"):
        "{platform_count} / {total} descargas",
    ("History", "no data"): "sin datos",
    ("History", "no channel data"): "sin datos de canal",
    ("History", "Showing {visible} of {total} download(s)"):
        "Mostrando {visible} de {total} descargas",
    ("History", "{downloads} matching download(s) • {hits} transcript hit(s)"):
        "{downloads} descargas coincidentes • {hits} coincidencias de transcripción",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                pieces.append("{" + ast.unparse(value.value) + "}")
        return "".join(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _template(node.left), _template(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _add(messages, locations, context, node, source, *, numerus=False, path=None):
    if not source or not source.strip():
        return
    message = Message(context, source, numerus)
    messages.add(message)
    if path is not None:
        locations[message].add((path.as_posix(), getattr(node, "lineno", 1)))


def extract_messages() -> tuple[set[Message], dict[Message, set[tuple[str, int]]]]:
    messages: set[Message] = set()
    locations: dict[Message, set[tuple[str, int]]] = defaultdict(set)
    for directory in SOURCE_DIRS:
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(ROOT)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = {
                        target.id for target in targets if isinstance(target, ast.Name)
                    }
                    value = node.value
                    if (
                        any("HEADER" in name for name in names)
                        and isinstance(value, (ast.List, ast.Tuple))
                    ):
                        for element in value.elts:
                            _add(
                                messages, locations, "StreamKeep", node,
                                _template(element), path=relative,
                            )
                    continue
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name in {"tr", "tr_n", "tr_format"} and node.args:
                    source = _template(node.args[0])
                    context = "StreamKeep"
                    for keyword in node.keywords:
                        if keyword.arg == "context":
                            context = _template(keyword.value) or context
                    _add(
                        messages, locations, context, node, source,
                        numerus=name == "tr_n", path=relative,
                    )
                    continue
                if name in FIRST_ARG_CALLS and node.args:
                    _add(
                        messages, locations, "StreamKeep", node,
                        _template(node.args[0]), path=relative,
                    )
                elif name in ALL_ARG_CALLS:
                    for argument in node.args:
                        _add(
                            messages, locations, "StreamKeep", node,
                            _template(argument), path=relative,
                        )
                    for keyword in node.keywords:
                        if keyword.arg in {"title", "body", "eyebrow", "badge_text"}:
                            _add(
                                messages, locations, "StreamKeep", node,
                                _template(keyword.value), path=relative,
                            )
                elif name in LIST_ARG_CALLS and node.args and isinstance(
                    node.args[0], (ast.List, ast.Tuple)
                ):
                    for element in node.args[0].elts:
                        _add(
                            messages, locations, "StreamKeep", node,
                            _template(element), path=relative,
                        )
                elif name in {"information", "warning", "critical", "question"}:
                    for argument in node.args[1:3]:
                        _add(
                            messages, locations, "StreamKeep", node,
                            _template(argument), path=relative,
                        )
    # Explicit dynamic contexts cannot always be inferred from a variable
    # passed to ``tr``.  The maintained core translations are catalog sources
    # too, so lrelease always receives those status/plural messages.
    for (context, source), translation in SPANISH_CORE.items():
        messages.add(Message(context, source, isinstance(translation, tuple)))
    return messages, locations


def _catalog_bytes(language: str) -> bytes:
    messages, locations = extract_messages()
    root = ET.Element("TS", {"version": "2.1", "language": language})
    by_context: dict[str, list[Message]] = defaultdict(list)
    for message in messages:
        by_context[message.context].append(message)
    for context_name in sorted(by_context):
        context = ET.SubElement(root, "context")
        ET.SubElement(context, "name").text = context_name
        for message in sorted(
            by_context[context_name],
            key=lambda item: (item.source.casefold(), item.source, item.numerus),
        ):
            attrs = {"numerus": "yes"} if message.numerus else {}
            element = ET.SubElement(context, "message", attrs)
            for filename, line in sorted(locations[message]):
                ET.SubElement(
                    element, "location", {"filename": filename, "line": str(line)}
                )
            ET.SubElement(element, "source").text = message.source
            translation = SPANISH_CORE.get((message.context, message.source))
            if language == "en":
                translation = (message.source, message.source) if message.numerus else message.source
            if message.numerus:
                target = ET.SubElement(
                    element, "translation", {} if translation else {"type": "unfinished"}
                )
                forms = translation if isinstance(translation, tuple) else ()
                for form in forms:
                    ET.SubElement(target, "numerusform").text = form
            else:
                target = ET.SubElement(
                    element, "translation", {} if translation else {"type": "unfinished"}
                )
                if isinstance(translation, str):
                    target.text = translation
    ET.indent(root, space="    ")
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return body.replace(b"'utf-8'", b'"utf-8"') + b"\n"


def update_catalogs(*, check: bool = False) -> bool:
    """Write catalogs, or return whether checked-in catalogs are current."""
    current = True
    for language in ("en", "es"):
        path = I18N_DIR / f"streamkeep_{language}.ts"
        expected = _catalog_bytes(language)
        if not path.exists() or path.read_bytes() != expected:
            current = False
            if not check:
                path.write_bytes(expected)
    return current if check else True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = update_catalogs(check=args.check)
    if args.check and not current:
        print("Translation catalogs are stale; run python -m streamkeep.i18n.extract_translations")
        return 1
    messages, _ = extract_messages()
    print(f"Translation catalogs cover {len(messages)} extracted UI/player messages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
