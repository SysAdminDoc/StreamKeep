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
from xml.sax.saxutils import escape as _xml_escape

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
    ("StreamKeep", "LOCAL ARCHIVE"): "ARCHIVO LOCAL",
    ("StreamKeep", "LOCAL-FIRST  •  v{version}"): "LOCAL PRIMERO  •  v{version}",
    ("StreamKeep", "Capture and queue streams, VODs, and media for local archiving."):
        "Capture y ponga en cola emisiones, VOD y archivos multimedia para archivarlos localmente.",
    ("StreamKeep", "Watch channels and automate reliable live capture."):
        "Vigile canales y automatice capturas en directo fiables.",
    ("StreamKeep", "Understand archive growth and capture patterns."):
        "Comprenda el crecimiento del archivo y los patrones de captura.",
    ("StreamKeep", "Inspect disk use, integrity, and recoverable cleanup."):
        "Revise el uso de disco, la integridad y la limpieza recuperable.",
    ("StreamKeep", "Tune local workflows, privacy, and integrations."):
        "Ajuste los flujos locales, la privacidad y las integraciones.",
    ("StreamKeep", "Named yt-dlp argument templates use one argv element per line. Only approved transfer, format, subtitle, and playlist options are accepted; executable paths, plugins, output/config delegation, and link writers are rejected. Imported templates stay disabled until you explicitly approve them. Templates can be attached in Download Advanced or a monitor channel profile."):
        "Las plantillas de argumentos de yt-dlp con nombre usan un elemento argv por línea. Solo se aceptan opciones aprobadas de transferencia, formato, subtítulos y listas de reproducción; se rechazan rutas de ejecutables, plugins, delegación de salida/configuración y escritores de enlaces. Las plantillas importadas permanecen desactivadas hasta que las apruebe explícitamente. Las plantillas se pueden adjuntar en Avanzado de Descarga o en el perfil de un canal supervisado.",
    ("StreamKeep", "Checking systems"): "Comprobando sistemas",
    ("StreamKeep", "Systems ready"): "Sistemas listos",
    ("StreamKeep", "Needs attention"): "Requiere atención",
    ("StreamKeep", "Open Settings to inspect local runtime and storage health"):
        "Abra Configuración para revisar el entorno local y el estado del almacenamiento",
    ("StreamKeep", "Local-only  •  {active_jobs} active  •  {queued} queued"):
        "Solo local  •  {active_jobs} activas  •  {queued} en cola",
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
    ("StreamKeep", "Resolve"): "Resolver",
    ("StreamKeep", "Resolving"): "Resolviendo",
    ("StreamKeep", "Resolve source"): "Resolver origen",
    ("StreamKeep", "Capture a source"): "Capturar un origen",
    ("StreamKeep", "Paste a stream, channel, VOD, podcast, or direct media URL to resolve it."):
        "Pegue una URL de emisión, canal, VOD, pódcast o archivo multimedia para resolverla.",
    ("StreamKeep", "Import"): "Importar",
    ("StreamKeep", "Import URLs"): "Importar URL",
    ("StreamKeep", "Paste"): "Pegar",
    ("StreamKeep", "Scan page"): "Escanear página",
    ("StreamKeep", "Scan page for media"):
        "Buscar contenido multimedia en la página",
    ("StreamKeep", "Allow LAN for this scan"): "Permitir LAN para este análisis",
    ("StreamKeep", "Allow LAN for next scan"):
        "Permitir LAN en el próximo análisis",
    ("StreamKeep", "Add URL to queue"): "Añadir URL a la cola",
    ("StreamKeep", "Download settings"): "Opciones de descarga",
    ("StreamKeep", "Your queue is clear"): "La cola está vacía",
    ("StreamKeep", "Resolve a source above or import a list to begin."):
        "Resuelva un origen arriba o importe una lista para comenzar.",
    ("StreamKeep", "Paste from clipboard"): "Pegar desde el portapapeles",
    ("StreamKeep", "Paste a source URL from the clipboard into the capture field"):
        "Pegue una URL de origen del portapapeles en el campo de captura",
    ("StreamKeep", "Import a list"): "Importar una lista",
    ("StreamKeep", "Empty download queue"): "Cola de descargas vacía",
    ("StreamKeep", "No activity events"): "Sin eventos de actividad",
    ("StreamKeep", "No activity yet"): "Aún no hay actividad",
    ("StreamKeep", "Events appear here as capture tasks run."):
        "Los eventos aparecen aquí mientras se ejecutan las capturas.",
    ("StreamKeep", "No events yet"): "Aún no hay eventos",
    ("StreamKeep", "Showing latest {count} event(s)"):
        "Mostrando los últimos {count} eventos",
    ("StreamKeep", "Archive health"): "Estado del archivo",
    ("StreamKeep", "Runtime"): "Entorno",
    ("StreamKeep", "Downloader"): "Descargador",
    ("StreamKeep", "Checking free space"): "Comprobando espacio libre",
    ("StreamKeep", "Checking FFmpeg"): "Comprobando FFmpeg",
    ("StreamKeep", "Checking yt-dlp"): "Comprobando yt-dlp",
    ("StreamKeep", "FFmpeg {version}"): "FFmpeg {version}",
    ("StreamKeep", "FFmpeg ready"): "FFmpeg listo",
    ("StreamKeep", "FFmpeg needs attention"): "FFmpeg requiere atención",
    ("StreamKeep", "Runtime ready"): "Entorno listo",
    ("StreamKeep", "Runtime needs attention"): "El entorno requiere atención",
    ("StreamKeep", "yt-dlp {version} ready"): "yt-dlp {version} listo",
    ("StreamKeep", "yt-dlp ready"): "yt-dlp listo",
    ("StreamKeep", "Downloader ready"): "Descargador listo",
    ("StreamKeep", "Downloader needs attention"): "El descargador requiere atención",
    ("StreamKeep", "{component} status is being checked"):
        "Comprobando el estado de {component}",
    ("StreamKeep", "View diagnostics"): "Ver diagnósticos",
    ("StreamKeep", "Open Settings and inspect the local toolchain"):
        "Abra Configuración y revise las herramientas locales",
    ("StreamKeep", "Storage monitoring off"): "Monitoreo de almacenamiento desactivado",
    ("StreamKeep", "{free_gb:.0f} GB free"): "{free_gb:.0f} GB libres",
    ("StreamKeep", "Per-download overrides"):
        "Ajustes específicos de la descarga",
    ("StreamKeep", "dub language: en"): "idioma doblado: en",
    ("StreamKeep", "Mute (video only)"): "Silenciar (solo vídeo)",
    ("StreamKeep", "Strip audio from the output while keeping the video track"):
        "Quite el audio de la salida y conserve la pista de vídeo",
    ("StreamKeep", "Prefer a dubbed yt-dlp audio track using an ISO 639-1 code such as en or es"):
        "Prefiera una pista de audio doblada de yt-dlp con un código ISO 639-1 como en o es",
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
    ("StreamKeep", "Your archive is ready"): "El archivo está listo",
    ("StreamKeep", "Completed captures will appear here with verification, search, and playback actions."):
        "Las capturas terminadas aparecerán aquí con verificación, búsqueda y reproducción.",
    ("StreamKeep", "Clear search"): "Borrar búsqueda",
    ("StreamKeep", "No matching downloads"): "No hay descargas coincidentes",
    ("StreamKeep", "Clear the current search or try a broader title, platform, channel, folder, or transcript phrase."):
        "Borre la búsqueda o pruebe un título, plataforma, canal, carpeta o frase de transcripción más general.",
    ("StreamKeep", "No downloads matched that search. Try a broader title, platform, channel, or folder term."):
        "Ninguna descarga coincide. Pruebe un título, plataforma, canal o carpeta más general.",
    # Settings workflow.
    ("StreamKeep", "Appearance"): "Apariencia",
    ("StreamKeep", "Channel monitoring"): "Monitoreo de canales",
    ("StreamKeep", "Track channels and automate capture."):
        "Monitoree canales y automatice la captura.",
    ("StreamKeep", "Download history"): "Historial de descargas",
    ("StreamKeep", "Search, verify, and reopen completed captures."):
        "Busque, verifique y vuelva a abrir capturas completadas.",
    ("StreamKeep", "Archive storage"): "Almacenamiento del archivo",
    ("StreamKeep", "Disk usage, maintenance, and safe cleanup."):
        "Uso de disco, mantenimiento y limpieza segura.",
    ("StreamKeep", "Archive analytics"): "Analíticas del archivo",
    ("StreamKeep", "Activity, storage, and source trends."):
        "Actividad, almacenamiento y tendencias de origen.",
    ("StreamKeep", "Appearance, downloads, privacy, and integrations."):
        "Apariencia, descargas, privacidad e integraciones.",
    ("StreamKeep", "Language"): "Idioma",
    ("StreamKeep", "English"): "Inglés",
    ("StreamKeep", "Español"): "Español",
    ("StreamKeep", "Dark"): "Oscuro",
    ("StreamKeep", "Light"): "Claro",
    ("StreamKeep", "System"): "Sistema",
    ("StreamKeep", "High Contrast"): "Alto contraste",
    ("StreamKeep", "Density"): "Densidad",
    ("StreamKeep", "Accent"): "Acento",
    ("StreamKeep", "Compact"): "Compacta",
    ("StreamKeep", "Cozy"): "Cómoda",
    ("StreamKeep", "Spacious"): "Espaciosa",
    ("StreamKeep", "Theme default"): "Predeterminado del tema",
    ("StreamKeep", "Theme"): "Tema",
    ("StreamKeep", "Default Output"): "Salida predeterminada",
    ("StreamKeep", "Local Toolchain"): "Herramientas locales",
    ("StreamKeep", "Save Settings"): "Guardar configuración",
    ("StreamKeep", "Settings saved and applied to future downloads."):
        "La configuración se guardó y se aplicará a futuras descargas.",
    ("StreamKeep", "Restore available unmuted audio in Twitch VODs"):
        "Restaurar el audio sin silenciar disponible en los VOD de Twitch",
    ("StreamKeep", "For finished Twitch VODs, probe same-format CDN fragments whose paths end in -muted and use the unmuted fragment when it exists. Unreachable fragments remain muted; live captures are not probed."):
        "En VOD de Twitch terminados, compruebe los fragmentos CDN del mismo formato cuyas rutas terminan en -muted y use el fragmento sin silenciar cuando exista. Los fragmentos inaccesibles permanecen silenciados; las capturas en directo no se comprueban.",
    # First-run states for monitored channels.
    ("StreamKeep", "AUTOMATED CAPTURE"): "CAPTURA AUTOMATIZADA",
    ("StreamKeep", "No channels on watch"): "No hay canales vigilados",
    ("StreamKeep", "Add a channel above, choose an interval, and optionally arm live auto-record."):
        "Añada un canal arriba, elija un intervalo y active opcionalmente la grabación automática.",
    ("StreamKeep", "Add your first channel"): "Añadir el primer canal",
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


def _xml_text(value: object) -> str:
    return _xml_escape(str(value))


def _xml_attribute(value: object) -> str:
    return _xml_escape(str(value), {"\"": "&quot;"})


def _xml_leaf(level: int, tag: str, text: object = None, attrs: str = "") -> str:
    indent = " " * (level * 4)
    if text is None or text == "":
        return f"{indent}<{tag}{attrs} />"
    return f"{indent}<{tag}{attrs}>{_xml_text(text)}</{tag}>"


def _catalog_bytes(language: str) -> bytes:
    messages, locations = extract_messages()
    by_context: dict[str, list[Message]] = defaultdict(list)
    for message in messages:
        by_context[message.context].append(message)
    lines = [
        "<?xml version='1.0' encoding=\"utf-8\"?>",
        (
            f'<TS version="2.1" language="{_xml_attribute(language)}">'
        ),
    ]
    for context_name in sorted(by_context):
        lines.append("    <context>")
        lines.append(_xml_leaf(2, "name", context_name))
        for message in sorted(
            by_context[context_name],
            key=lambda item: (item.source.casefold(), item.source, item.numerus),
        ):
            message_attrs = ' numerus="yes"' if message.numerus else ""
            lines.append(f"        <message{message_attrs}>")
            for filename, line in sorted(locations[message]):
                lines.append(
                    _xml_leaf(
                        3, "location", attrs=(
                            f' filename="{_xml_attribute(filename)}"'
                            f' line="{_xml_attribute(line)}"'
                        ),
                    )
                )
            lines.append(_xml_leaf(3, "source", message.source))
            translation = SPANISH_CORE.get((message.context, message.source))
            if language == "en":
                translation = (message.source, message.source) if message.numerus else message.source
            if message.numerus:
                translation_attrs = "" if translation else ' type="unfinished"'
                forms = translation if isinstance(translation, tuple) else ()
                if forms:
                    lines.append(f"            <translation{translation_attrs}>")
                    lines.extend(_xml_leaf(4, "numerusform", form) for form in forms)
                    lines.append("            </translation>")
                else:
                    lines.append(_xml_leaf(3, "translation", attrs=translation_attrs))
            else:
                if isinstance(translation, str):
                    lines.append(_xml_leaf(3, "translation", translation))
                else:
                    lines.append(
                        _xml_leaf(3, "translation", attrs=' type="unfinished"')
                    )
            lines.append("        </message>")
        lines.append("    </context>")
    lines.append("</TS>")
    return ("\n".join(lines) + "\n").encode("utf-8")


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
