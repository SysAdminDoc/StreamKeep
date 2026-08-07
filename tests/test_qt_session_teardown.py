"""Guards for the Qt session teardown that V177's access violation traced to.

The crash had no Python-level symptom — the suite reported every test as passed
and then died in native code during session-fixture finalisation. These tests
pin the two properties that removed it, so a future edit to ``conftest`` that
reintroduces the fault fails here instead of as an intermittent segfault.
"""

import gc
import inspect

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

import conftest


def test_the_application_outlives_the_fixture_local(qt_application):
    """The session fixture must not hold the only reference to the app.

    When it did, returning from the fixture destroyed the QApplication while
    widgets were still alive, and Qt tore those down under a dead application.
    """
    assert conftest._QT_APP is qt_application
    assert QApplication.instance() is qt_application


def test_the_session_fixture_actually_runs_the_retirement():
    """The behaviour tests below call the helper directly, so this asserts the
    fixture is still wired to it — otherwise unwiring the teardown would leave
    every other test in this module green."""
    source = inspect.getsource(conftest.qt_application._get_wrapped_function())
    body_after_yield = source.split("yield", 1)[1]
    assert "_retire_qt_objects" in body_after_yield


def test_retirement_closes_leftover_top_level_widgets(qt_application):
    widget = QWidget()
    widget.show()
    assert widget in qt_application.topLevelWidgets()

    conftest._retire_qt_objects(qt_application)

    # deleteLater() has been delivered, so the C++ object is gone and the
    # application no longer lists it. Touching the wrapper now raises.
    assert not any(w is widget for w in qt_application.topLevelWidgets())


def test_retirement_stops_a_thread_that_a_test_left_running(qt_application):
    class _Idle(QThread):
        def run(self):
            self.exec()

    thread = _Idle()
    thread.start()
    assert thread.wait(0) is False  # still running

    conftest._retire_qt_objects(qt_application)

    assert not thread.isRunning()
    thread.wait(1000)


def test_retirement_survives_a_wrapper_whose_c_object_is_gone(qt_application):
    """A deleted QThread wrapper still reachable from gc must not break teardown."""

    class _Doomed(QThread):
        pass

    doomed = _Doomed()
    doomed.deleteLater()
    qt_application.processEvents()
    gc.collect()

    conftest._retire_qt_objects(qt_application)  # must not raise
