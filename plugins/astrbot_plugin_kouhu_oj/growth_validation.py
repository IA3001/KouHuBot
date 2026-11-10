"""Sandboxed checks of one reproducible C++ counterexample, not semantic proof.

No compiler or candidate executable is ever launched outside bubblewrap. Output
and diagnostics are file-backed and bounded by RLIMIT_FSIZE, including compiles.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import signal
import tempfile

MAX_CODE = 12_000
MAX_STDIN = 10_000
MAX_OUTPUT = 1_048_576
_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
# Keep literal contents and token boundaries while ignoring formatting/comments.
_TOKENS = re.compile(
    r'R"(?P<delimiter>[^ ()\\\t\r\n]{0,16})\(.*?\)(?P=delimiter)"'
    r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|//[^\n]*|/\*.*?\*/|[A-Za-z_0-9]+'
    r'|>>=|<<=|<=>|->\*|\.\.\.|::|->|\.\*|\+\+|--|<<|>>|<=|>=|==|!='
    r'|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|##|[^\s]', re.DOTALL)


def _normalized(code: str) -> tuple:
    return tuple(m.group() for m in _TOKENS.finditer(code)
                 if not m.group().startswith(("//", "/*")))


def _contains(program: str, snippet: str) -> bool:
    full, part = _normalized(program), _normalized(snippet)
    return bool(part) and any(full[i:i + len(part)] == part
                              for i in range(len(full) - len(part) + 1))


def _limits() -> None:
    """Apply to bwrap and all descendants before executing any command."""
    for kind, value in ((resource.RLIMIT_CPU, 12),
                        (resource.RLIMIT_AS, 768 * 1024 * 1024),
                        (resource.RLIMIT_NPROC, 32),
                        (resource.RLIMIT_FSIZE, MAX_OUTPUT),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (value, value))


def _sandbox(bwrap: str, work: Path, command: list[str]) -> list[str]:
    if bwrap == 'landlock':
        try:
            from .growth_sandbox import sandbox_prefix
        except ImportError:
            from growth_sandbox import sandbox_prefix
        return sandbox_prefix(work) + [arg.replace('/work/',str(work)+'/') for arg in command]
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", _ENV["PATH"],
            "--setenv", "LANG", "C"]
    for mount in ("/usr", "/lib", "/lib64"):
        if Path(mount).exists():
            args.extend(["--ro-bind", mount, mount])
    args.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--bind", str(work), "/work", "--chdir", "/work", "--"])
    return args + command


def _execute(bwrap: str, work: Path, command: list[str], *, timeout: int,
             stdin: bytes = b"") -> dict:
    # Capture files live outside the mounted work directory: candidate code
    # cannot replace them with symlinks or tamper with another stage's evidence.
    with tempfile.TemporaryFile() as input_file, tempfile.TemporaryFile() as out, \
            tempfile.TemporaryFile() as err:
        input_file.write(stdin)
        input_file.seek(0)
        try:
            process = subprocess.Popen(
                _sandbox(bwrap, work, command), stdin=input_file, stdout=out,
                stderr=err, env=dict(_ENV), cwd=str(work), start_new_session=True,
                preexec_fn=_limits, close_fds=True, shell=False)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return {"error":"timeout"}
            finally:
                # Kill the full process group, including background descendants.
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        except subprocess.TimeoutExpired:
            return {'error':'timeout'}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {"error": "sandbox_launch_failed", "detail": type(exc).__name__}
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(MAX_OUTPUT), err.read(MAX_OUTPUT)
        try:
            decoded_stdout = stdout.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "invalid_stdout_encoding", "returncode": process.returncode}
        return {"returncode": process.returncode,
                "stdout": decoded_stdout,
                "stderr": stderr.decode("utf-8", errors="replace"),
                "output_limit": len(stdout) >= MAX_OUTPUT or len(stderr) >= MAX_OUTPUT}


def _schema_error(item: dict) -> str:
    if not isinstance(item, dict):
        return "candidate must be a dict"
    for name in ("id", "category", "title", "question", "buggy_code", "answer", "fix"):
        value = item.get(name)
        bound = MAX_CODE if name in ("buggy_code", "fix") else 12_000
        if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > bound:
            return f"invalid or oversized {name}"
    level = item.get("level")
    if (isinstance(level, bool) or not isinstance(level, (str, int))
            or (isinstance(level, str) and (not level.strip() or len(level) > 64))):
        return "invalid level"
    if item.get("cpp_standard") not in ("c++17", "c++20", "c++23"):
        return "unsupported cpp_standard"
    validation = item.get("validation")
    if not isinstance(validation, dict):
        return "validation must be a dict"
    for name, limit in (("buggy_program", MAX_CODE), ("fixed_program", MAX_CODE),
                        ("stdin", MAX_STDIN), ("expected_stdout", MAX_OUTPUT)):
        value = validation.get(name)
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            return f"invalid or oversized validation.{name}"
        if name.endswith("program") and not value.strip():
            return f"empty validation.{name}"
    if validation.get("buggy_kind") not in ("wrong_output", "compile_error"):
        return "unsupported buggy_kind"
    if not _contains(validation["buggy_program"], item["buggy_code"]):
        return "buggy_code snippet mismatch"
    if not _contains(validation["fixed_program"], item["fix"]):
        return "fix snippet mismatch"
    # The driver must be identical: a generated test cannot fake success by
    # replacing unrelated logic or simply printing its expected answer.
    buggy = _normalized(validation['buggy_program'])
    fixed = _normalized(validation['fixed_program'])
    before = _normalized(item['buggy_code'])
    after = _normalized(item['fix'])
    replacements = [buggy[:i] + after + buggy[i+len(before):]
                    for i in range(len(buggy)-len(before)+1)
                    if buggy[i:i+len(before)] == before]
    if fixed not in replacements:
        return 'test driver differs beyond the shown fix'
    return ""


def validate_candidate(item: dict) -> dict:
    """Return {ok, reason, evidence}; acceptance covers only the supplied input."""
    evidence = {"scope": "reproducible_counterexample_only", "semantic_proof": False}

    def result(ok: bool, reason: str) -> dict:
        return {"ok": ok, "reason": reason, "evidence": evidence}

    try:
        error = _schema_error(item)
    except (UnicodeError, ValueError, TypeError):
        error = "invalid candidate encoding or schema"
    if error:
        return result(False, error)
    bwrap, compiler = shutil.which("bwrap"), shutil.which("g++")
    if not bwrap or not compiler:
        return result(False, "sandbox unavailable: bwrap and g++ are required")
    # Only tools available through the explicitly mounted system tree are usable.
    compiler = os.path.realpath(compiler)
    if not compiler.startswith("/usr/"):
        return result(False, "sandbox compiler must reside under /usr")
    validation = item["validation"]
    try:
        with tempfile.TemporaryDirectory(prefix="kouhu-validation-") as root:
            root = Path(root)
            probe = root / "probe"
            probe.mkdir()
            evidence["isolation"] = _execute(bwrap, probe, [compiler, "--version"], timeout=15)
            if ("error" in evidence["isolation"]
                    or evidence["isolation"].get("returncode") != 0
                    or evidence["isolation"].get("output_limit")):
                bwrap = 'landlock'
                evidence['isolation'] = _execute(bwrap, probe, [compiler, '--version'],timeout=15)
                if 'error' in evidence['isolation'] or evidence['isolation'].get('returncode') != 0:
                    return result(False, 'sandbox isolation unavailable')
            for variant in ("fixed", "buggy"):
                work = root / variant
                work.mkdir()
                (work / "program.cpp").write_text(validation[variant + "_program"], encoding="utf-8")
                (work / variant).touch(mode=0o700)
                compile_result = _execute(
                    bwrap, work, [compiler, "-std=" + item["cpp_standard"], "-O0",
                                  "/work/program.cpp", "-o", "/work/" + variant], timeout=15)
                evidence[variant + "_compile"] = compile_result
                if "error" in compile_result or compile_result.get("output_limit"):
                    return result(False, variant + " compilation could not be validated")
                status = compile_result["returncode"]
                if variant == "buggy" and validation["buggy_kind"] == "compile_error":
                    # g++ uses exit 1 for compilation diagnostics. Signals, bwrap
                    # failures and other driver failures are not compile errors.
                    diagnostics = compile_result["stderr"]
                    if status == 1 and "error:" in diagnostics and "bwrap:" not in diagnostics:
                        return result(True, "reproducible compile-error counterexample")
                    return result(False, "buggy program did not demonstrate a compiler error")
                if status != 0:
                    return result(False, variant + " program must compile successfully")
                # Landlock helper denies chmod to protected system paths; parent sets
                # executable mode only on the fixed local output after compilation.
                binary=work / variant
                if binary.is_symlink() or not binary.is_file():
                    return result(False,'compiler output is not a regular binary')
                binary.chmod(0o700)
                execution = _execute(bwrap, work, ["/work/" + variant], timeout=2,
                                     stdin=validation["stdin"].encode("utf-8"))
                evidence[variant + "_run"] = execution
                if ("error" in execution or execution.get("returncode") != 0
                        or execution.get("output_limit")):
                    return result(False, variant + " program must exit successfully within limits")
                matches = execution["stdout"].rstrip("\n") == validation["expected_stdout"].rstrip("\n")
                if variant == "fixed" and not matches:
                    return result(False, "fixed output does not match expected_stdout")
                if variant == "buggy" and matches:
                    return result(False, "buggy output does not demonstrate a counterexample")
            return result(True, "reproducible wrong-output counterexample")
    except (OSError, UnicodeError, ValueError) as exc:
        evidence["error"] = type(exc).__name__
        return result(False, "validation infrastructure failed")
