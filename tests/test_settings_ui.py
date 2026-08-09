from types import SimpleNamespace

from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QVBoxLayout, QWidget

from streamkeep.ui.tabs.settings_search import SettingsSearchController


def _section(root, title, label_text, *, tooltip="", setting_key=""):
    section = QFrame(root)
    layout = QVBoxLayout(section)
    layout.addWidget(QLabel(title, section))
    layout.addWidget(QLabel(label_text, section))
    control = QLineEdit(section)
    control.setToolTip(tooltip)
    if setting_key:
        control.setProperty("settingKey", setting_key)
    layout.addWidget(control)
    return section, control


def test_settings_search_matches_label_tooltip_and_setting_key(qt_application):
    root = QWidget()
    layout = QVBoxLayout(root)
    general, output = _section(
        root,
        "Default Output",
        "Archive folder",
        tooltip="Where completed downloads are stored",
        setting_key="output_dir",
    )
    network, proxy = _section(
        root,
        "Network",
        "Proxy",
        tooltip="Use a SOCKS proxy for network requests",
        setting_key="proxy",
    )
    layout.addWidget(general)
    layout.addWidget(network)
    owner = SimpleNamespace(
        _config={"output_dir": "", "proxy": ""},
        output_input=output,
        proxy_input=proxy,
    )
    controller = SettingsSearchController(
        root,
        (("General", general), ("Network", network)),
        owner=owner,
    )
    root.show()
    qt_application.processEvents()

    controller.filter("archive folder")
    assert output.isVisible()
    assert not proxy.isVisible()
    assert controller.visible_section_names == ("General",)

    controller.filter("SOCKS")
    assert not output.isVisible()
    assert proxy.isVisible()
    assert controller.visible_section_names == ("Network",)

    controller.filter("output_dir")
    assert output.isVisible()
    assert not proxy.isVisible()

    controller.filter("")
    assert output.isVisible()
    assert proxy.isVisible()
    assert controller.visible_section_names == ("General", "Network")
    root.close()
