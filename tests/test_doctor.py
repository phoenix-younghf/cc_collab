from __future__ import annotations

from unittest.mock import patch
from unittest import TestCase

from runtime.doctor import run_doctor


class DoctorTests(TestCase):
    def test_doctor_fails_when_claude_missing(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "claude",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)

    def test_doctor_fails_when_ccollab_missing_even_if_path_contains_bin_dir(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "ccollab",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)
        self.assertIn("ccollab", [check.name for check in report.checks])

    def test_doctor_checks_required_claude_flags(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda flag: flag != "--json-schema",
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)

    def test_doctor_accepts_windows_python_launcher(self) -> None:
        report = run_doctor(
            os_name="nt",
            command_exists=lambda name: name != "python3",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertTrue(report.ok)

    def test_doctor_normalizes_windows_path_entries(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "USERPROFILE": r"C:\Users\Steven",
                "APPDATA": r"C:\Users\Steven\AppData\Roaming",
                "PATH": r"c:\users\steven\.local\bin\;",
            },
            clear=True,
        ):
            report = run_doctor(
                os_name="nt",
                command_exists=lambda _name: True,
                flag_probe=lambda _flag: True,
                writable_probe=lambda _path: True,
            )
        self.assertTrue(report.ok)
