from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_module("codex_request_builder_dispatch", "scripts/build_codex_engineering_request.py")
dispatcher = load_module("codex_dispatcher", "scripts/dispatch_codex_worker.py")
validator = load_module("architect_continuation_codex", "scripts/validate_architect_continuation.py")


def contract(objective: str = "bounded fix") -> dict[str, object]:
    return {
        "enabled": True,
        "contract_id": "C-EXACT-1",
        "target_repository": "mezas3238-hue/qore-core",
        "objective": objective,
        "scope": ["src/qore/example.py"],
        "acceptance": ["behavior is deterministic"],
        "required_tests": ["ruff", "mypy", "pytest"],
        "forbidden": ["Production authority"],
    }


def engineering_decision(objective: str = "bounded fix") -> dict[str, object]:
    return {
        "schema_version": "qore.architect.decision.v1",
        "source_main_sha": "a" * 40,
        "status": "ENGINEERING_TASK",
        "next_actor": "CODEX",
        "engineering_contract": contract(objective),
        "production_authority": False,
    }


def disabled_engineering() -> dict[str, object]:
    return {
        "enabled": False,
        "contract_id": "",
        "target_repository": "",
        "objective": "",
        "scope": [],
        "acceptance": [],
        "required_tests": [],
        "forbidden": [],
    }


def disabled_review() -> dict[str, object]:
    return {
        "enabled": False,
        "contract_id": "",
        "pr_number": 0,
        "review_kind": "NONE",
        "objective": "",
        "scope": [],
        "adversarial_foci": [],
        "acceptance": [],
        "forbidden": [],
    }


class CodexDispatchControlPlaneTests(unittest.TestCase):
    def test_package_identity_binds_exact_contract_content(self) -> None:
        first = builder.build(engineering_decision("fix alpha"), "11")
        same = builder.build(engineering_decision("fix alpha"), "99")
        changed = builder.build(engineering_decision("fix beta"), "11")
        self.assertEqual(first["package_id"], same["package_id"])
        self.assertNotEqual(first["package_id"], changed["package_id"])
        self.assertRegex(first["package_id"], dispatcher.PACKAGE_RE)

    def test_worker_target_remains_qore_core_only(self) -> None:
        decision = engineering_decision()
        decision["engineering_contract"]["target_repository"] = "mezas3238-hue/qore-deepseek-reviewer"  # type: ignore[index]
        with self.assertRaises(ValueError):
            builder.build(decision, "1")

    def test_run_name_parser_requires_exact_package_shape(self) -> None:
        package = builder.build(engineering_decision(), "1")["package_id"]
        self.assertEqual(
            dispatcher.package_for_run({"display_title": f"Codex worker · {package}"}),
            package,
        )
        self.assertIsNone(dispatcher.package_for_run({"display_title": f"prefix {package}"}))
        self.assertIsNone(dispatcher.package_for_run({"display_title": "Codex worker · malformed"}))

    def test_codex_wait_requires_exact_active_package(self) -> None:
        package = builder.build(engineering_decision(), "1")["package_id"]
        decision = {
            "source_main_sha": "a" * 40,
            "status": "WAITING_AGENT",
            "next_actor": "NONE",
            "engineering_contract": disabled_engineering(),
            "review_contract": disabled_review(),
            "wait_state": {
                "enabled": True,
                "actor": "CODEX",
                "package_id": package,
                "reason": "Codex worker is executing the exact package",
            },
            "production_authority": False,
        }
        snapshot = {
            "main_sha": "a" * 40,
            "codex_worker_state": {
                "active_runs": [{"package_id": package, "status": "in_progress"}],
            },
        }
        validator.validate(decision, snapshot)
        snapshot["codex_worker_state"]["active_runs"][0]["package_id"] = "QORE-CODEX-bbbbbbbbbbbb-0123456789abcdef"  # type: ignore[index]
        with self.assertRaises(ValueError):
            validator.validate(decision, snapshot)

    def test_completed_codex_run_is_not_a_wait_boundary(self) -> None:
        package = builder.build(engineering_decision(), "1")["package_id"]
        decision = {
            "source_main_sha": "a" * 40,
            "status": "WAITING_AGENT",
            "next_actor": "NONE",
            "engineering_contract": disabled_engineering(),
            "review_contract": disabled_review(),
            "wait_state": {
                "enabled": True,
                "actor": "CODEX",
                "package_id": package,
                "reason": "supposed wait",
            },
            "production_authority": False,
        }
        snapshot = {
            "main_sha": "a" * 40,
            "codex_worker_state": {
                "active_runs": [],
                "latest_completed": {"package_id": package, "status": "completed", "conclusion": "success"},
            },
        }
        with self.assertRaises(ValueError):
            validator.validate(decision, snapshot)


if __name__ == "__main__":
    unittest.main()
