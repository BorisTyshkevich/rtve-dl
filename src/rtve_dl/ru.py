from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rtve_dl.log import debug, stage


def _find_py313() -> str | None:
    """
    Prefer a Python 3.13 interpreter for Argos Translate (avoids upstream issues on 3.14+).
    """
    for name in ("python3.13", "python3"):
        p = subprocess.run([name, "-c", "import sys; print(sys.version_info[:2])"], capture_output=True, text=True)
        if p.returncode != 0:
            continue
        if "(3, 13)" in p.stdout:
            return name
    return None


def ensure_argos_venv(root: Path) -> Path:
    """
    Ensure a dedicated venv exists for Argos Translate and return its python path.
    """
    venv_dir = root / ".venv_argos"
    py = venv_dir / "bin" / "python"
    pip = venv_dir / "bin" / "pip"
    if py.exists():
        return py

    py313 = _find_py313()
    if py313 is None:
        raise RuntimeError("python3.13 not found; Argos Translate requires Python 3.13 in this project")

    with stage("argos:venv:create"):
        subprocess.check_call([py313, "-m", "venv", str(venv_dir)])
    with stage("argos:venv:deps"):
        subprocess.check_call([str(pip), "install", "--upgrade", "pip"])
        # Keep this inside the dedicated venv to avoid impacting the main environment.
        subprocess.check_call([str(pip), "install", "argostranslate>=1.9.6"])
    return py


def setup_argos_model(root: Path, *, model_path: str | None) -> None:
    py = ensure_argos_venv(root)
    runner = root / "tools" / "argos_runner.py"
    cmd = [str(py), str(runner), "setup"]
    if model_path:
        cmd += ["--model", model_path]
    with stage("argos:model:setup"):
        subprocess.check_call(cmd)


def translate_cues_jsonl(
    root: Path,
    *,
    cues: list[tuple[str, str]],
    src: str,
    dst: str,
    out_jsonl: Path,
) -> None:
    """
    cues: list of (id, text)
    """
    py = ensure_argos_venv(root)
    runner = root / "tools" / "argos_runner.py"
    inp = out_jsonl.with_suffix(".in.jsonl")
    inp.parent.mkdir(parents=True, exist_ok=True)

    with inp.open("w", encoding="utf-8") as f:
        for _id, text in cues:
            f.write(json.dumps({"id": _id, "text": text}, ensure_ascii=False) + "\n")

    cmd = [
        str(py),
        str(runner),
        "translate",
        "--in-jsonl",
        str(inp),
        "--out-jsonl",
        str(out_jsonl),
        "--from",
        src,
        "--to",
        dst,
    ]
    debug("argos translate via: " + " ".join(cmd))
    with stage("argos:translate"):
        subprocess.check_call(cmd)
