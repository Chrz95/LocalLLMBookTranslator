"""Queue filtering and numeric sorting preserve chunk identities and selections."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from gui import TranslatorGUI, queue_view


def row(index, status="Pending", check="", bookmark="", source="", translation=""):
    return {"index": index, "status": status, "check": check, "bookmark": bookmark,
            "unit": "Chapter", "detail": "", "source": source,
            "translation": translation, "label": ""}


class QueueViewTests(unittest.TestCase):
    def test_numeric_sort_and_stable_text_ties(self):
        rows = [row(10, "Error"), row(2, "Completed"), row(1, "Error")]
        self.assertEqual(queue_view(rows), [1, 2, 10])
        self.assertEqual(queue_view(rows, descending=True), [10, 2, 1])
        self.assertEqual(queue_view(rows, sort="status"), [2, 1, 10])
        self.assertEqual(queue_view(rows, sort="status", descending=True), [1, 10, 2])
        self.assertEqual([entry["index"] for entry in rows], [10, 2, 1])

    def test_combined_filters_and_blank_review(self):
        rows = [row(0, "Completed", "Checked", "★", translation="Hello"),
                row(1, "Completed", "Issues found", "★", translation="HELLO"),
                row(2, "Pending", source="Hello"), row(3, "Completed", "Checked")]
        self.assertEqual(queue_view(rows, status="Completed", review="Checked",
                                    bookmarks_only=True, query=" hello "), [0])
        self.assertEqual(queue_view(rows, review="Not checked"), [2])
        self.assertEqual(queue_view(rows, query="missing"), [])
        self.assertEqual(queue_view(rows, bookmarks_only=True, sort="#0", descending=True), [1, 0])

    def test_text_in_source_translation_labels_and_details(self):
        rows = [row(0, source="Καλημέρα"), row(1, translation="Bonjour")]
        rows[1]["label"] = "Revisit finale"
        rows[0]["detail"] = "Connection failed"
        for query, expected in (("ΚΑΛΗΜΈΡΑ", [0]), ("BONJOUR", [1]),
                                ("finale", [1]), ("failed", [0]), ("1", [1])):
            self.assertEqual(queue_view(rows, query=query), expected)


class MemoryTree:
    """Minimal Treeview behavior for detached rows and hidden selection."""
    def __init__(self):
        self.values = {"0": row(0), "1": row(1, "Completed")}
        self.visible = ["0", "1"]
        self.selected = ["0"]

    def set(self, item):
        return self.values[item]

    def get_children(self):
        return tuple(self.visible)

    def selection(self):
        return tuple(self.selected)

    def selection_remove(self, *items):
        self.selected = [item for item in self.selected if item not in items]

    def selection_set(self, items):
        self.selected = list(items)

    def detach(self, *items):
        self.visible = [item for item in self.visible if item not in items]

    def move(self, item, parent, position):
        if item in self.visible:
            self.visible.remove(item)
        self.visible.insert(position, item)

    def focus(self, item=None):
        return self.selected[0] if item is None and self.selected else ""

    def see(self, item):
        assert item in self.visible


class QueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.gui = TranslatorGUI.__new__(TranslatorGUI)
        self.gui.root = Mock()
        self.gui.tree = MemoryTree()
        self.gui.job = SimpleNamespace(chunks=[SimpleNamespace(index=i, text="Source") for i in range(2)])
        self.gui.previews = {0: "First", 1: "Second"}
        self.gui.bookmarks = {}
        self.gui.queue_refresh_after = None
        self.gui.queue_sort, self.gui.queue_descending = "#0", False
        for name, value in (("queue_status", "Completed"), ("queue_review", "All"),
                            ("queue_bookmarked", False), ("queue_query", "")):
            variable = Mock()
            variable.get.side_effect = lambda variable=variable: variable.value
            variable.set.side_effect = lambda value, variable=variable: setattr(variable, "value", value)
            variable.value = value
            setattr(self.gui, name, variable)
        self.gui.queue_visible = Mock()
        self.gui.source_preview, self.gui.target_preview = Mock(), Mock()
        self.gui.selection_changed = Mock()
        self.gui._set_text = Mock()

    def test_filter_detaches_rows_deselects_hidden_and_restores_rows(self):
        self.gui.refresh_queue_view()
        self.assertEqual(self.gui.tree.visible, ["1"])
        self.assertEqual(self.gui.tree.selection(), ())
        self.assertEqual(len(self.gui.tree.values), 2)
        self.gui.queue_status.set("All")
        self.gui.refresh_queue_view()
        self.assertEqual(self.gui.tree.visible, ["0", "1"])

    def test_jump_reveals_hidden_chunk_and_selects_original_id(self):
        self.gui.select_queue_chunks([0])
        self.assertEqual(self.gui.queue_status.get(), "All")
        self.assertEqual(self.gui.tree.selection(), ("0",))
        self.assertIn("0", self.gui.tree.visible)

    def test_status_update_changes_filtered_membership(self):
        self.gui.refresh_queue_view()
        self.gui.tree.values["0"]["status"] = "Completed"
        self.gui.refresh_queue_view()
        self.assertEqual(self.gui.tree.visible, ["0", "1"])


if __name__ == "__main__":
    unittest.main()
