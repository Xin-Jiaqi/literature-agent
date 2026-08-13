#!/usr/bin/env python3
"""LaunchAgent 模板回归检查：日志不得放在 macOS TCC 保护的项目目录。"""
import os
import plistlib
import tempfile


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTOMATION = os.path.join(BASE, "automation")


def _render(template_name, log_dir):
    path = os.path.join(AUTOMATION, template_name)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("__BASE__", BASE).replace("__LOG_DIR__", log_dir)
    return plistlib.loads(text.encode("utf-8"))


def run_all():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = os.path.join(tmp, "Library", "Logs", "literature-agent")
        cases = (
            ("com.jiaqi.literature-agent.plist.template", "launchd.out.log", "launchd.err.log"),
            ("com.jiaqi.literature-agent-sync.plist.template", "sync.launchd.out.log", "sync.launchd.err.log"),
        )
        for template, stdout_name, stderr_name in cases:
            plist = _render(template, log_dir)
            assert plist["StandardOutPath"] == os.path.join(log_dir, stdout_name)
            assert plist["StandardErrorPath"] == os.path.join(log_dir, stderr_name)
            assert BASE not in plist["StandardOutPath"]
            assert BASE not in plist["StandardErrorPath"]
            assert "__BASE__" not in str(plist)
            assert "__LOG_DIR__" not in str(plist)
    return True


if __name__ == "__main__":
    print("automation launchd test:", "PASS" if run_all() else "FAIL")
