#!/usr/bin/env python3
"""Build a deterministic, allowlisted paper-triage distribution (stdlib only)."""
import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def build(output):
    root = Path(__file__).resolve().parents[1]
    members = {
        "paper-triage/SKILL.md": root / "skills/paper-triage/SKILL.md",
        "paper-triage/LICENSE": root / "LICENSE",
    }
    # Read all inputs before touching an output. Never recursively archive the repo.
    payloads = {name: path.read_bytes() for name, path in members.items()}
    skill = payloads["paper-triage/SKILL.md"].decode("utf-8")
    if not skill.startswith("---\nname: paper-triage\ndescription: "):
        raise ValueError("Unexpected skill identity/frontmatter; review before packaging")
    output = Path(output).resolve()
    if output in {path.resolve() for path in members.values()}:
        raise ValueError("Output must not overwrite a source file")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse replacement: callers choose a new filename for another build.
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, payload in sorted(payloads.items()):
            entry = ZipInfo(name, date_time=(2026, 11, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payload)
    with ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(members):
            raise ValueError("Archive validation failed")
        for name, payload in payloads.items():
            if archive.read(name) != payload:
                raise ValueError("Packaged bytes differ from source")
    print(output)
    print("SHA256", hashlib.sha256(output.read_bytes()).hexdigest())
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]
                        / "reports/release/paper-triage-v0.1.0.zip")
    build(parser.parse_args().output)
