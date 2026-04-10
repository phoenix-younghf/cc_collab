from __future__ import annotations

from unittest.mock import patch
from unittest import TestCase

from runtime.doctor import render_doctor_report, run_doctor


class DoctorTests(TestCase):
    def test_doctor_fails_when_python_missing(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name not in {"python", "python3", "py"},
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(check.name == "python" and check.severity == "error" for check in report.checks)
        )

    def test_doctor_fails_when_claude_missing(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "claude",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)

    def test_doctor_fails_when_ccollab_missing_even_if_path_contains_bin_dir(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "ccollab",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (False, "launcher invocation failed"),
        )
        self.assertFalse(report.ok)
        self.assertIn("launcher", [check.name for check in report.checks])

    def test_doctor_checks_required_claude_flags(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda flag: flag != "--json-schema",
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)

    def test_doctor_accepts_windows_python_launcher(self) -> None:
        report = run_doctor(
            os_name="nt",
            command_exists=lambda name: name != "python3",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertTrue(report.ok)

    def test_doctor_warns_when_git_missing_but_keeps_runtime_ready(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "git",
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertTrue(report.ok)
        self.assertTrue(
            any(check.name == "git" and check.severity == "warning" for check in report.checks)
        )

    def test_doctor_warns_when_launcher_directory_is_not_on_path(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: False,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertTrue(report.ok)
        self.assertTrue(
            any(check.name == "path" and check.severity == "warning" for check in report.checks)
        )

    def test_doctor_renders_readiness_sections(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        rendered = render_doctor_report(report)
        self.assertIn("Install Readiness", rendered)
        self.assertIn("Runtime Readiness", rendered)
        self.assertIn("Enhanced Safety Capability", rendered)

    def test_doctor_renders_actionable_remediation(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name not in {"claude", "git", "python", "python3", "py"},
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: False,
            launcher_probe=lambda: (False, "launcher invocation failed"),
        )
        rendered = render_doctor_report(report)
        self.assertIn("Install Claude CLI", rendered)
        self.assertIn("Add", rendered)
        self.assertIn("PATH", rendered)

    def test_doctor_fails_when_launcher_is_broken(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (False, "launcher invocation failed"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(check.name == "launcher" and check.severity == "error" for check in report.checks)
        )

    def test_doctor_fails_when_required_directory_is_unwritable(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda path: path.name not in {"bin", "cc_collab"},
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                check.name in {"skill-dir", "bin-dir", "config-dir", "task-root"}
                and check.severity == "error"
                for check in report.checks
            )
        )

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
                launcher_probe=lambda: (True, "launcher ok"),
            )
        self.assertTrue(report.ok)
