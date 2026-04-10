"""Settings tab — the biggest tab (510+ lines of field blocks).

Groups: default output, toolchain probe, cookies, network + rate limit +
bandwidth schedule + parallel connections, YouTube extras, templates,
webhook, dedup, media library, post-processing + converter, manual
converter buttons, import/export/save row.
"""

import subprocess

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ... import VERSION
from ...extractors import Extractor
from ...extractors.twitch import TwitchExtractor
from ...extractors.ytdlp import YtDlpExtractor
from ...http import set_native_proxy
from ...paths import _CREATE_NO_WINDOW, CONFIG_FILE
from ...postprocess import (
    AUDIO_CODECS, AUDIO_CONTAINERS, PostProcessor,
    VIDEO_CONTAINERS, available_video_codec_keys,
)
from ...theme import CAT
from ...utils import (
    DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE,
    default_output_dir as _default_output_dir,
)
from ..widgets import make_field_block, make_metric_card


def build_settings_tab(win):
    """Build the Settings tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)

    # ── Hero ────────────────────────────────────────────────────────
    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(18, 18, 18, 18)
    hero_lay.setSpacing(14)

    hero_copy = QVBoxLayout()
    hero_copy.setSpacing(4)
    kicker = QLabel("Settings")
    kicker.setObjectName("eyebrow")
    title = QLabel("Tune storage, authenticated access, and tooling")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Set default output behavior, attach browser cookies for gated "
        "content, and verify the local toolchain that powers downloads."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    settings_meta = QLabel(
        f"StreamKeep v{VERSION}\n"
        f"Config file: {CONFIG_FILE}\n"
        f"Supported platforms: {', '.join(Extractor.all_names())}"
    )
    settings_meta.setObjectName("sectionBody")
    settings_meta.setWordWrap(True)
    hero_lay.addWidget(settings_meta)
    lay.addWidget(hero)

    # ── Card body ───────────────────────────────────────────────────
    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(18, 18, 18, 18)
    card_lay.setSpacing(14)

    # Default Output + Toolchain (side by side)
    sections_top = QHBoxLayout()
    sections_top.setSpacing(12)

    general_block, general_lay = make_field_block(
        "Default Output", "New downloads will default to this folder."
    )
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.settings_output = QLineEdit(str(_default_output_dir()))
    output_row.addWidget(win.settings_output, 1)
    browse = QPushButton("Browse")
    browse.setObjectName("secondary")
    browse.clicked.connect(lambda: win._settings_browse(win.settings_output))
    output_row.addWidget(browse)
    general_lay.addLayout(output_row)
    sections_top.addWidget(general_block, 1)

    tools_block, tools_lay = make_field_block(
        "Local Toolchain",
        "StreamKeep relies on these binaries for robust downloads.",
    )
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        ff_ver = r.stdout.split("\n")[0] if r.returncode == 0 else "Not found"
    except Exception:
        ff_ver = "Not found"
    try:
        r = subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        yt_ver = f"yt-dlp {r.stdout.strip()}" if r.returncode == 0 else "Not installed"
    except Exception:
        yt_ver = "Not installed"
    ff_card, _, _ = make_metric_card(
        "ffmpeg",
        "Ready" if ff_ver != "Not found" else "Missing",
        ff_ver[:48],
    )
    yt_card, _, _ = make_metric_card(
        "yt-dlp",
        "Ready" if yt_ver != "Not installed" else "Missing",
        yt_ver[:48],
    )
    tools_metrics = QHBoxLayout()
    tools_metrics.setSpacing(10)
    tools_metrics.addWidget(ff_card)
    tools_metrics.addWidget(yt_card)
    tools_lay.addLayout(tools_metrics)
    sections_top.addWidget(tools_block, 1)
    card_lay.addLayout(sections_top)

    # ── Cookies ─────────────────────────────────────────────────────
    cookies_block, cookies_lay = make_field_block(
        "Browser Cookies",
        "Use browser cookies or a cookies.txt file for age-restricted or "
        "authenticated content.",
    )

    row_cookies = QHBoxLayout()
    row_cookies.setSpacing(8)
    win.cookies_combo = QComboBox()
    win.cookies_combo.addItem("None")
    row_cookies.addWidget(win.cookies_combo, 1)
    scan_btn = QPushButton("Scan for Browsers")
    scan_btn.setObjectName("secondary")
    scan_btn.clicked.connect(win._on_scan_browsers)
    row_cookies.addWidget(scan_btn)
    cookies_lay.addLayout(row_cookies)

    row_cookiefile = QHBoxLayout()
    row_cookiefile.setSpacing(8)
    win.cookies_file_input = QLineEdit()
    win.cookies_file_input.setPlaceholderText("Path to cookies.txt (Netscape format)")
    row_cookiefile.addWidget(win.cookies_file_input, 1)
    browse_cookies = QPushButton("Browse")
    browse_cookies.setObjectName("secondary")
    browse_cookies.clicked.connect(win._on_browse_cookies_file)
    row_cookiefile.addWidget(browse_cookies)
    cookies_lay.addLayout(row_cookiefile)

    win.cookies_scan_label = QLabel("")
    win.cookies_scan_label.setObjectName("subtleText")
    win.cookies_scan_label.setWordWrap(True)
    cookies_lay.addWidget(win.cookies_scan_label)
    card_lay.addWidget(cookies_block)

    saved_browser = win._config.get("cookies_browser", "")
    saved_file = win._config.get("cookies_file", "")
    if saved_file:
        win.cookies_file_input.setText(saved_file)
        YtDlpExtractor.cookies_file = saved_file
    win._scan_browsers_silent()
    if saved_browser:
        idx = win.cookies_combo.findText(saved_browser)
        if idx >= 0:
            win.cookies_combo.setCurrentIndex(idx)
        YtDlpExtractor.cookies_browser = saved_browser

    # ── Network ────────────────────────────────────────────────────
    network_block, network_lay = make_field_block(
        "Network",
        "Optional bandwidth throttling and proxy for geo-blocked content.",
    )
    rate_row = QHBoxLayout()
    rate_row.setSpacing(8)
    rate_label = QLabel("Rate limit:")
    rate_label.setFixedWidth(100)
    rate_row.addWidget(rate_label)
    win.rate_limit_input = QLineEdit()
    win.rate_limit_input.setPlaceholderText("e.g. 500K or 2M (leave blank for unlimited)")
    rate_row.addWidget(win.rate_limit_input, 1)
    network_lay.addLayout(rate_row)

    proxy_row = QHBoxLayout()
    proxy_row.setSpacing(8)
    proxy_label = QLabel("Proxy URL:")
    proxy_label.setFixedWidth(100)
    proxy_row.addWidget(proxy_label)
    win.proxy_input = QLineEdit()
    win.proxy_input.setPlaceholderText("e.g. socks5://127.0.0.1:1080 or http://proxy:8080")
    proxy_row.addWidget(win.proxy_input, 1)
    network_lay.addLayout(proxy_row)

    # Bandwidth schedule
    win.bw_enable_check = QCheckBox(
        "Enable bandwidth schedule (overrides Rate limit within the window)"
    )
    win.bw_enable_check.setChecked(win._bandwidth_rule["enabled"])
    network_lay.addWidget(win.bw_enable_check)
    bw_row = QHBoxLayout()
    bw_row.setSpacing(8)
    bw_row.addWidget(QLabel("Window:"))
    win.bw_start_spin = QSpinBox()
    win.bw_start_spin.setRange(0, 23)
    win.bw_start_spin.setSuffix(":00")
    win.bw_start_spin.setValue(win._bandwidth_rule["start_hour"])
    bw_row.addWidget(win.bw_start_spin)
    bw_row.addWidget(QLabel("to"))
    win.bw_end_spin = QSpinBox()
    win.bw_end_spin.setRange(0, 23)
    win.bw_end_spin.setSuffix(":00")
    win.bw_end_spin.setValue(win._bandwidth_rule["end_hour"])
    bw_row.addWidget(win.bw_end_spin)
    bw_row.addSpacing(12)
    bw_row.addWidget(QLabel("Limit:"))
    win.bw_limit_input = QLineEdit(win._bandwidth_rule["limit"])
    win.bw_limit_input.setPlaceholderText("500K")
    win.bw_limit_input.setFixedWidth(100)
    bw_row.addWidget(win.bw_limit_input)
    bw_row.addStretch(1)
    network_lay.addLayout(bw_row)

    # Parallel connections per direct MP4
    par_row = QHBoxLayout()
    par_row.setSpacing(8)
    par_label = QLabel("Parallel connections:")
    par_label.setFixedWidth(140)
    par_row.addWidget(par_label)
    win.parallel_spin = QSpinBox()
    win.parallel_spin.setRange(1, 16)
    win.parallel_spin.setValue(win._parallel_connections)
    win.parallel_spin.setToolTip(
        "Multi-connection HTTP Range splitting for direct MP4 files.\n"
        "Higher values can be 3-5x faster on CDN-hosted content.\n"
        "Set to 1 to disable and always use ffmpeg."
    )
    par_row.addWidget(win.parallel_spin)
    par_hint = QLabel("per direct MP4 (1 = off, default 4)")
    par_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
    par_row.addWidget(par_hint)
    par_row.addStretch(1)
    network_lay.addLayout(par_row)

    # Load saved network settings
    saved_rate = win._config.get("rate_limit", "")
    saved_proxy = win._config.get("proxy", "")
    if saved_rate:
        win.rate_limit_input.setText(saved_rate)
        YtDlpExtractor.rate_limit = saved_rate
    if saved_proxy:
        win.proxy_input.setText(saved_proxy)
        YtDlpExtractor.proxy = saved_proxy
        set_native_proxy(saved_proxy)

    card_lay.addWidget(network_block)

    # ── YouTube extras ─────────────────────────────────────────────
    yt_block, yt_lay = make_field_block(
        "YouTube Extras", "Optional yt-dlp features for YouTube videos."
    )
    win.subs_check = QCheckBox("Download subtitles (English) and embed in video")
    win.sponsorblock_check = QCheckBox(
        "Skip SponsorBlock segments (sponsor / self-promo / interaction)"
    )
    yt_lay.addWidget(win.subs_check)
    yt_lay.addWidget(win.sponsorblock_check)

    if win._config.get("download_subs"):
        win.subs_check.setChecked(True)
        YtDlpExtractor.download_subs = True
    if win._config.get("sponsorblock"):
        win.sponsorblock_check.setChecked(True)
        YtDlpExtractor.sponsorblock = True

    card_lay.addWidget(yt_block)

    # ── Filename templates ─────────────────────────────────────────
    tpl_block, tpl_lay = make_field_block(
        "Filename Templates",
        "Variables: {title} {channel} {platform} {date} {year} {month} {day}. "
        "Use / to create subfolders. Each segment is sanitized.",
    )
    folder_row = QHBoxLayout()
    folder_row.setSpacing(8)
    folder_label = QLabel("Folder:")
    folder_label.setFixedWidth(100)
    folder_row.addWidget(folder_label)
    win.folder_template_input = QLineEdit(win._folder_template)
    win.folder_template_input.setPlaceholderText(DEFAULT_FOLDER_TEMPLATE)
    folder_row.addWidget(win.folder_template_input, 1)
    tpl_lay.addLayout(folder_row)
    file_row = QHBoxLayout()
    file_row.setSpacing(8)
    file_label = QLabel("Filename:")
    file_label.setFixedWidth(100)
    file_row.addWidget(file_label)
    win.file_template_input = QLineEdit(win._file_template)
    win.file_template_input.setPlaceholderText(DEFAULT_FILE_TEMPLATE)
    file_row.addWidget(win.file_template_input, 1)
    tpl_lay.addLayout(file_row)
    card_lay.addWidget(tpl_block)

    # ── Webhook ────────────────────────────────────────────────────
    hook_block, hook_lay = make_field_block(
        "Webhook Notifications",
        "POST a JSON payload when downloads complete. Discord webhook URLs "
        "are auto-detected and formatted as embeds.",
    )
    win.webhook_input = QLineEdit(win._webhook_url)
    win.webhook_input.setPlaceholderText(
        "https://discord.com/api/webhooks/... or any POST endpoint"
    )
    hook_lay.addWidget(win.webhook_input)
    card_lay.addWidget(hook_block)

    # ── Duplicate detection ────────────────────────────────────────
    dup_block, dup_lay = make_field_block(
        "Duplicate Detection",
        "Warn before downloading something already in your history.",
    )
    win.dup_check = QCheckBox("Check history for URL and title matches before download")
    win.dup_check.setChecked(win._check_duplicates)
    dup_lay.addWidget(win.dup_check)
    card_lay.addWidget(dup_block)

    # ── Media Library ──────────────────────────────────────────────
    lib_block, lib_lay = make_field_block(
        "Media Library",
        "Write Kodi/Jellyfin/Plex-compatible metadata files and chat replays "
        "for archival.",
    )
    win.nfo_check = QCheckBox("Write .nfo file (movie schema) alongside each download")
    win.nfo_check.setChecked(win._write_nfo)
    lib_lay.addWidget(win.nfo_check)
    win.chat_check = QCheckBox("Download Twitch VOD chat replay (JSON + plain text)")
    win.chat_check.setChecked(TwitchExtractor.download_chat_enabled)
    lib_lay.addWidget(win.chat_check)
    card_lay.addWidget(lib_block)

    # ── Post-Processing ────────────────────────────────────────────
    pp_block, pp_lay = make_field_block(
        "Post-Processing",
        "Automatic ffmpeg operations on each downloaded file. Originals are preserved.",
    )
    win.pp_audio_check = QCheckBox("Extract audio as MP3 (libmp3lame, VBR quality 2)")
    win.pp_audio_check.setChecked(PostProcessor.extract_audio)
    pp_lay.addWidget(win.pp_audio_check)
    win.pp_loud_check = QCheckBox("Normalize loudness (EBU R128: I=-16, TP=-1.5, LRA=11)")
    win.pp_loud_check.setChecked(PostProcessor.normalize_loudness)
    pp_lay.addWidget(win.pp_loud_check)
    win.pp_h265_check = QCheckBox("Re-encode video to H.265/HEVC (libx265, CRF 23 — slow)")
    win.pp_h265_check.setChecked(PostProcessor.reencode_h265)
    pp_lay.addWidget(win.pp_h265_check)
    win.pp_contact_check = QCheckBox("Generate contact sheet (3x3 thumbnail grid .jpg)")
    win.pp_contact_check.setChecked(PostProcessor.contact_sheet)
    pp_lay.addWidget(win.pp_contact_check)
    win.pp_split_check = QCheckBox(
        "Split by chapters into per-chapter files (for videos with chapters)"
    )
    win.pp_split_check.setChecked(PostProcessor.split_by_chapter)
    pp_lay.addWidget(win.pp_split_check)

    # Video converter row
    win.pp_convert_video_check = QCheckBox("Convert video to:")
    win.pp_convert_video_check.setChecked(PostProcessor.convert_video)
    win.pp_convert_video_format = QComboBox()
    win.pp_convert_video_format.addItems(VIDEO_CONTAINERS)
    idx = (
        VIDEO_CONTAINERS.index(PostProcessor.convert_video_format)
        if PostProcessor.convert_video_format in VIDEO_CONTAINERS else 0
    )
    win.pp_convert_video_format.setCurrentIndex(idx)
    win.pp_convert_video_format.setFixedWidth(80)
    win.pp_convert_video_codec = QComboBox()
    vc_keys = available_video_codec_keys()
    win.pp_convert_video_codec.addItems(vc_keys)
    saved_vc = PostProcessor.convert_video_codec
    if saved_vc in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index(saved_vc))
    elif "h264" in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index("h264"))
    win.pp_convert_video_codec.setFixedWidth(140)
    hw_count = sum(1 for k in vc_keys if "(" in k)
    hw_note = (
        f" ({hw_count} GPU encoder{'s' if hw_count != 1 else ''} detected)"
        if hw_count else ""
    )
    win.pp_convert_video_codec.setToolTip(
        "copy = fast remux (no re-encode)\n"
        "h264/h265/vp9/av1/mpeg4 = software encoders\n"
        "(NVENC) = NVIDIA GPU (5-20x faster)\n"
        "(QSV) = Intel Quick Sync\n"
        "(AMF) = AMD GPU\n"
        "(VT) = Apple VideoToolbox\n"
        + hw_note
    )
    # Scale target
    scale_items = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
    win.pp_convert_video_scale = QComboBox()
    win.pp_convert_video_scale.addItems(scale_items)
    idx = (
        scale_items.index(PostProcessor.convert_video_scale)
        if PostProcessor.convert_video_scale in scale_items else 0
    )
    win.pp_convert_video_scale.setCurrentIndex(idx)
    win.pp_convert_video_scale.setFixedWidth(90)
    win.pp_convert_video_scale.setToolTip(
        "Downscale target height. Aspect ratio is preserved.\n"
        "Forces a re-encode when not 'original' (copy codec ignored)."
    )
    # FPS cap
    fps_items = ["original", "60", "30", "24"]
    win.pp_convert_video_fps = QComboBox()
    win.pp_convert_video_fps.addItems(fps_items)
    idx = (
        fps_items.index(PostProcessor.convert_video_fps)
        if PostProcessor.convert_video_fps in fps_items else 0
    )
    win.pp_convert_video_fps.setCurrentIndex(idx)
    win.pp_convert_video_fps.setFixedWidth(80)
    win.pp_convert_video_fps.setToolTip(
        "Frame rate cap. Forces a re-encode when not 'original'."
    )

    vconv_row = QHBoxLayout()
    vconv_row.setSpacing(6)
    vconv_row.addWidget(win.pp_convert_video_check)
    vconv_row.addSpacing(4)
    vconv_row.addWidget(QLabel("Container:"))
    vconv_row.addWidget(win.pp_convert_video_format)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Codec:"))
    vconv_row.addWidget(win.pp_convert_video_codec)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Scale:"))
    vconv_row.addWidget(win.pp_convert_video_scale)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("FPS:"))
    vconv_row.addWidget(win.pp_convert_video_fps)
    vconv_row.addStretch(1)
    pp_lay.addLayout(vconv_row)

    # Audio converter row
    win.pp_convert_audio_check = QCheckBox("Convert audio to:")
    win.pp_convert_audio_check.setChecked(PostProcessor.convert_audio)
    win.pp_convert_audio_format = QComboBox()
    win.pp_convert_audio_format.addItems(AUDIO_CONTAINERS)
    idx = (
        AUDIO_CONTAINERS.index(PostProcessor.convert_audio_format)
        if PostProcessor.convert_audio_format in AUDIO_CONTAINERS else 0
    )
    win.pp_convert_audio_format.setCurrentIndex(idx)
    win.pp_convert_audio_format.setFixedWidth(80)
    win.pp_convert_audio_codec = QComboBox()
    win.pp_convert_audio_codec.addItems(list(AUDIO_CODECS.keys()))
    ac_keys = list(AUDIO_CODECS.keys())
    idx = (
        ac_keys.index(PostProcessor.convert_audio_codec)
        if PostProcessor.convert_audio_codec in ac_keys else 1
    )
    win.pp_convert_audio_codec.setCurrentIndex(idx)
    win.pp_convert_audio_codec.setFixedWidth(90)
    win.pp_convert_audio_codec.setToolTip(
        "copy = remux only\n"
        "mp3 = libmp3lame (universal)\n"
        "aac = AAC-LC (Apple-friendly)\n"
        "opus = low-bitrate champion\n"
        "vorbis = open-source lossy\n"
        "flac/pcm = lossless"
    )
    win.pp_convert_audio_bitrate = QComboBox()
    win.pp_convert_audio_bitrate.addItems(["96k", "128k", "192k", "256k", "320k"])
    br_items = ["96k", "128k", "192k", "256k", "320k"]
    idx = (
        br_items.index(PostProcessor.convert_audio_bitrate)
        if PostProcessor.convert_audio_bitrate in br_items else 2
    )
    win.pp_convert_audio_bitrate.setCurrentIndex(idx)
    win.pp_convert_audio_bitrate.setFixedWidth(80)
    win.pp_convert_audio_bitrate.setToolTip("Bitrate (ignored for flac/pcm)")
    # Sample rate
    sr_items = ["original", "48000", "44100", "22050"]
    win.pp_convert_audio_samplerate = QComboBox()
    win.pp_convert_audio_samplerate.addItems(sr_items)
    idx = (
        sr_items.index(PostProcessor.convert_audio_samplerate)
        if PostProcessor.convert_audio_samplerate in sr_items else 0
    )
    win.pp_convert_audio_samplerate.setCurrentIndex(idx)
    win.pp_convert_audio_samplerate.setFixedWidth(90)
    win.pp_convert_audio_samplerate.setToolTip(
        "Sample rate (Hz). Forces a re-encode when not 'original'."
    )

    aconv_row = QHBoxLayout()
    aconv_row.setSpacing(6)
    aconv_row.addWidget(win.pp_convert_audio_check)
    aconv_row.addSpacing(4)
    aconv_row.addWidget(QLabel("Container:"))
    aconv_row.addWidget(win.pp_convert_audio_format)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Codec:"))
    aconv_row.addWidget(win.pp_convert_audio_codec)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Bitrate:"))
    aconv_row.addWidget(win.pp_convert_audio_bitrate)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Rate:"))
    aconv_row.addWidget(win.pp_convert_audio_samplerate)
    aconv_row.addStretch(1)
    pp_lay.addLayout(aconv_row)

    win.pp_convert_delete_check = QCheckBox(
        "Delete original source file after successful conversion"
    )
    win.pp_convert_delete_check.setChecked(PostProcessor.convert_delete_source)
    pp_lay.addWidget(win.pp_convert_delete_check)

    # Standalone manual converter
    manual_row = QHBoxLayout()
    manual_row.setSpacing(8)
    win.convert_files_btn = QPushButton("Convert Files...")
    win.convert_files_btn.setObjectName("secondary")
    win.convert_files_btn.setToolTip(
        "Pick individual media files and convert them with the current settings.\n"
        "Saves your settings first."
    )
    win.convert_files_btn.clicked.connect(win._on_convert_files_clicked)
    manual_row.addWidget(win.convert_files_btn)

    win.convert_folder_btn = QPushButton("Convert Folder...")
    win.convert_folder_btn.setObjectName("secondary")
    win.convert_folder_btn.setToolTip(
        "Pick a folder; every video/audio file in it gets converted."
    )
    win.convert_folder_btn.clicked.connect(win._on_convert_folder_clicked)
    manual_row.addWidget(win.convert_folder_btn)

    win.convert_cancel_btn = QPushButton("Cancel")
    win.convert_cancel_btn.setObjectName("secondary")
    win.convert_cancel_btn.setVisible(False)
    win.convert_cancel_btn.clicked.connect(win._on_convert_cancel)
    manual_row.addWidget(win.convert_cancel_btn)
    manual_row.addStretch(1)
    pp_lay.addLayout(manual_row)

    card_lay.addWidget(pp_block)

    # Save / Import / Export row
    save_row = QHBoxLayout()
    import_btn = QPushButton("Import Config")
    import_btn.setObjectName("secondary")
    import_btn.setToolTip("Replace current settings with a backup file")
    import_btn.clicked.connect(win._on_import_config)
    save_row.addWidget(import_btn)
    export_btn = QPushButton("Export Config")
    export_btn.setObjectName("secondary")
    export_btn.setToolTip("Write current settings to a backup file")
    export_btn.clicked.connect(win._on_export_config)
    save_row.addWidget(export_btn)
    save_row.addStretch()
    save_btn = QPushButton("Save Settings")
    save_btn.setObjectName("primary")
    save_btn.clicked.connect(win._on_save_settings)
    save_row.addWidget(save_btn)
    card_lay.addLayout(save_row)

    lay.addWidget(card, 1)
    return page
