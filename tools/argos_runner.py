#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def cmd_setup(model: str | None) -> int:
    import argostranslate.package
    import argostranslate.translate

    def have_pair(src: str, dst: str) -> bool:
        installed = argostranslate.translate.get_installed_languages()
        for lang in installed:
            if lang.code != src:
                continue
            for tr in (lang.translations_to or []):
                try:
                    if tr.to_lang.code == dst:
                        return True
                except Exception:
                    continue
        return False

    if model:
        argostranslate.package.install_from_path(model)
        print("ok")
        return 0

    argostranslate.package.update_package_index()
    pkgs = argostranslate.package.get_available_packages()
    # Prefer direct es->ru if available, otherwise install pivot packages es->en and en->ru.
    need: list[tuple[str, str]] = []
    if not have_pair("es", "ru"):
        direct = next((p for p in pkgs if p.from_code == "es" and p.to_code == "ru"), None)
        if direct is not None:
            need.append(("es", "ru"))
        else:
            need.append(("es", "en"))
            need.append(("en", "ru"))

    for src, dst in need:
        if have_pair(src, dst):
            continue
        pkg = next((p for p in pkgs if p.from_code == src and p.to_code == dst), None)
        if pkg is None:
            raise SystemExit(f"Argos model not found for {src}->{dst}")
        download_path = pkg.download()
        argostranslate.package.install_from_path(download_path)

    print("ok")
    return 0


def cmd_translate(in_jsonl: str, out_jsonl: str) -> int:
    import argostranslate.translate

    inp = Path(in_jsonl)
    outp = Path(out_jsonl)
    outp.parent.mkdir(parents=True, exist_ok=True)

    with inp.open("r", encoding="utf-8") as fin, outp.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            _id = obj["id"]
            text = obj.get("text") or ""
            # Use Argos pivoting if direct es->ru isn't installed.
            ru = argostranslate.translate.translate(text, "es", "ru") if text.strip() else ""
            fout.write(json.dumps({"id": _id, "ru": ru}, ensure_ascii=False) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--model", default=None, help="Optional path to a local .argosmodel file")

    p = sub.add_parser("translate")
    p.add_argument("--in-jsonl", required=True)
    p.add_argument("--out-jsonl", required=True)

    args = ap.parse_args()
    if args.cmd == "setup":
        return cmd_setup(args.model)
    if args.cmd == "translate":
        return cmd_translate(args.in_jsonl, args.out_jsonl)
    raise SystemExit("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
