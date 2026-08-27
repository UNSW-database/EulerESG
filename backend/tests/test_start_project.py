import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start_project.py"
SPEC = importlib.util.spec_from_file_location("start_project_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
start_project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(start_project)


class StartProjectTests(unittest.TestCase):
    def test_frontend_port_validation(self):
        with patch.dict(os.environ, {"FRONTEND_PORT": "3101"}, clear=False):
            self.assertEqual(start_project._frontend_port(), 3101)

        for invalid in ("invalid", "0", "65536"):
            with (
                self.subTest(invalid=invalid),
                patch.dict(os.environ, {"FRONTEND_PORT": invalid}, clear=False),
                self.assertRaises(ValueError),
            ):
                start_project._frontend_port()

    def test_resolves_an_installed_nvm_lts_version_without_path(self):
        executable_name = "node.exe" if os.name == "nt" else "node"
        with tempfile.TemporaryDirectory() as temporary_directory:
            nvm_home = Path(temporary_directory)
            lts_node = nvm_home / "v22.22.0" / executable_name
            current_node = nvm_home / "v25.2.1" / executable_name
            lts_node.parent.mkdir(parents=True)
            current_node.parent.mkdir(parents=True)
            lts_node.touch()
            current_node.touch()

            with (
                patch.dict(
                    os.environ,
                    {
                        "FRONTEND_NODE": "",
                        "NVM_HOME": str(nvm_home),
                        "NVM_SYMLINK": "",
                    },
                    clear=False,
                ),
                patch.object(start_project.shutil, "which", return_value=None),
            ):
                resolved = start_project._resolve_node_executable()

        self.assertEqual(resolved, lts_node.resolve())

    def test_starts_next_with_the_discovered_node_and_no_shell_lookup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            frontend_dir = temporary_root / "frontend"
            next_cli = frontend_dir / "node_modules" / "next" / "dist" / "bin" / "next"
            next_cli.parent.mkdir(parents=True)
            next_cli.touch()
            node_executable = temporary_root / ("node.exe" if os.name == "nt" else "node")
            node_executable.touch()
            process = MagicMock()

            with (
                patch.object(start_project, "FRONTEND_DIR", frontend_dir),
                patch.object(start_project, "frontend_is_healthy", return_value=False),
                patch.object(
                    start_project,
                    "_resolve_node_executable",
                    return_value=node_executable,
                ),
                patch.object(start_project, "_frontend_port", return_value=3101),
                patch.object(start_project.subprocess, "Popen", return_value=process) as popen,
            ):
                result = start_project.start_frontend()

        self.assertIs(result, process)
        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(
            command,
            [str(node_executable), str(next_cli), "dev", "-p", "3101"],
        )
        self.assertEqual(options["cwd"], str(frontend_dir))
        self.assertNotIn("shell", options)
        self.assertEqual(
            options["env"]["PATH"].split(os.pathsep, 1)[0],
            str(node_executable.parent),
        )

    def test_starts_backend_with_a_resolved_python_environment(self):
        backend_python = Path("C:/runtime/python.exe")
        process = MagicMock()
        with (
            patch.object(start_project, "backend_is_healthy", return_value=False),
            patch.object(
                start_project,
                "_resolve_backend_python",
                return_value=backend_python,
            ),
            patch.object(start_project.subprocess, "Popen", return_value=process) as popen,
        ):
            result = start_project.start_backend()

        self.assertIs(result, process)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(backend_python))
        self.assertEqual(Path(command[1]).name, "start_backend.py")

    def test_resolves_the_first_python_with_backend_dependencies(self):
        missing = Path("C:/runtime/base/python.exe")
        ready = Path("C:/runtime/envs/ES/python.exe")
        with (
            patch.dict(os.environ, {"BACKEND_PYTHON": ""}, clear=False),
            patch.object(
                start_project,
                "_backend_python_candidates",
                return_value=iter((missing, ready)),
            ),
            patch.object(Path, "is_file", return_value=True),
            patch.object(
                start_project,
                "_python_has_backend_dependencies",
                side_effect=lambda candidate: candidate == ready,
            ),
        ):
            resolved = start_project._resolve_backend_python()

        self.assertEqual(resolved, ready.resolve())

    def test_wait_for_frontend_reports_an_early_exit(self):
        process = MagicMock()
        process.poll.return_value = 7
        process.returncode = 7
        with (
            patch.object(start_project, "frontend_is_healthy", return_value=False),
            self.assertRaisesRegex(RuntimeError, "exit_code=7"),
        ):
            start_project.wait_for_frontend(process, 5)

    def test_main_is_console_safe_when_both_services_already_run(self):
        output = io.StringIO()
        with (
            patch.object(start_project, "start_backend", return_value=None),
            patch.object(start_project, "wait_for_backend"),
            patch.object(start_project, "start_frontend", return_value=None),
            patch.object(start_project, "wait_for_frontend"),
            redirect_stdout(output),
        ):
            exit_code = start_project.main()

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("系统启动完成", rendered)
        self.assertIn("无需重复启动", rendered)
        for unsafe_symbol in ("🚀", "✅", "🔧", "🎨", "⏳", "❌", "👋"):
            self.assertNotIn(unsafe_symbol, rendered)


if __name__ == "__main__":
    unittest.main()
