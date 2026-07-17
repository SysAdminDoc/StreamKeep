from PyQt6.QtCore import Qt

from streamkeep.storage import StorageFile, StorageGroup
from streamkeep.ui.storage_model import StorageFilterProxyModel, StorageTableModel


def test_storage_model_filters_large_group_sets_without_cell_widgets(qt_application):
    groups = [
        StorageGroup(
            dir_path=f"C:/archive/{index}",
            title=f"Recording {index}",
            platform="Twitch" if index % 2 else "YouTube",
            channel=f"channel-{index % 10}",
            files=[StorageFile(path=f"C:/archive/{index}/video.mp4", size=1024)],
            total_size=1024,
            newest_mtime=float(index),
        )
        for index in range(100_000)
    ]
    model = StorageTableModel()
    proxy = StorageFilterProxyModel()
    proxy.setSourceModel(model)
    model.set_groups(groups)

    assert model.rowCount() == 100_000
    assert proxy.rowCount() == 100_000
    assert model.data(model.index(5, 3)) == "Recording 5"
    assert model.data(model.index(5, 0), Qt.ItemDataRole.UserRole) is groups[5]

    proxy.set_filters("Twitch", "channel-3")
    assert proxy.rowCount() == 10_000
    assert all(
        proxy.group_at(row).platform == "Twitch"
        and proxy.group_at(row).channel == "channel-3"
        for row in range(min(25, proxy.rowCount()))
    )
