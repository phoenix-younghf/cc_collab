from __future__ import annotations

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

    def test_doctor_checks_required_claude_flags(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda flag: flag != "--json-schema",
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)
