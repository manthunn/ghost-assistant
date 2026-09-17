"""Text-field selection checks without touching real windows."""
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from ghost.skills import ui_automation as ui


def field(name, value="existing"):
    c = Mock(element_info=NS(control_type="Edit"))
    c.window_text.return_value = name
    c.get_value.return_value = value
    return c


class FieldTests(unittest.TestCase):
    def test_missing_name_never_falls_back_to_first_field(self):
        other = field("Password")
        win = Mock()
        win.descendants.return_value = [other]
        with patch.object(ui, "_find_window", return_value=win):
            self.assertIn("No text field", ui.clear_control("app", "Search"))
            self.assertIn("No text field", ui.type_into_control("app", "Search", "query"))
        other.set_focus.assert_not_called()
        other.set_edit_text.assert_not_called()

    def test_blank_and_ambiguous_names_are_rejected(self):
        win = Mock()
        win.descendants.return_value = [field("Search messages"), field("Search contacts")]
        for name in ("", "Search"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ui._edit_by_name(win, name)

    def test_exact_name_wins_over_partial_matches(self):
        exact = field("Search")
        win = Mock()
        win.descendants.return_value = [field("Search messages"), exact]
        self.assertIs(ui._edit_by_name(win, "Search"), exact)

    def test_failed_clear_prevents_paste(self):
        target, win = field("Search"), Mock()
        win.descendants.return_value = [target]
        with patch.object(ui, "_find_window", return_value=win), patch.object(ui, "_clear_edit", return_value=False):
            self.assertIn("no replacement text", ui.type_into_control("app", "Search", "query"))
        target.type_keys.assert_not_called()

    def test_clear_checks_value_instead_of_accessibility_name(self):
        target = field("Search", "")
        keyboard = NS(send_keys=Mock())
        with patch.dict(sys.modules, {"pywinauto.keyboard": keyboard}):
            self.assertTrue(ui._clear_edit(target))
        keyboard.send_keys.assert_not_called()

    def test_whitespace_is_not_an_empty_field(self):
        target = field("Search", "   ")
        keyboard = NS(send_keys=Mock())
        with patch.dict(sys.modules, {"pywinauto.keyboard": keyboard}), patch.object(ui.time, "sleep"):
            self.assertFalse(ui._clear_edit(target))


if __name__ == "__main__":
    unittest.main()
