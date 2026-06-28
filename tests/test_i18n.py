from streamkeep import i18n


def test_compiled_spanish_translation_is_available(qt_application):
    assert "es" in i18n.available_languages()
    assert i18n.install_translator("es", qt_application)
    assert i18n.current_language() == "es"
    assert i18n.install_translator("en", qt_application)
    assert i18n.current_language() == "en"
