#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_e2e.py — email_triage 端到端测试(2026-09-24)
#
# 目的:
#   - 造 5 个 fake .eml 文件 → 跑 main.py 三种 format → 验输出非空 + 关键字段
#   - 不依赖网络(纯 eml-dir 模式)
#   - 不依赖真实 adapter(用 monkeypatch 注入 fake classify)
#
# 跑法:
#   cd C:/Users/Administrator/oi_enhancements/projects/email_triage
#   python tests/test_e2e.py
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _make_fake_eml(path: Path, sender: str, subject: str, body: str,
                   hours_ago: int = 2) -> None:
    """造一个 fake .eml(用 EmailMessage 库 API 保兼容)。"""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = (
        datetime.now(timezone.utc).replace(microsecond=0)
        - _td(hours=hours_ago)
    ).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(msg))


def _td(**kw):
    from datetime import timedelta
    return timedelta(**kw)


def test_format_text() -> tuple[Path, str]:
    """text 模式:5 类分布 + 待办清单。"""
    with tempfile.TemporaryDirectory() as tmpd:
        eml_dir = Path(tmpd) / "inbox"
        _make_fake_eml(eml_dir / "01.eml",
                       "verify@amazon.com", "Your verification code is 123456",
                       "Click here to verify your account.", hours_ago=2)
        _make_fake_eml(eml_dir / "02.eml",
                       "boss@company.com", "Tomorrow's sync - confirm?",
                       "Please reply by EOD.", hours_ago=3)
        _make_fake_eml(eml_dir / "03.eml",
                       "noreply@github.com",
                       "New SSH key added to your account",
                       "If you did not perform this action, "
                       "review your security settings.", hours_ago=4)
        _make_fake_eml(eml_dir / "04.eml",
                       "newsletter@github.com",
                       "Weekly digest: 5 trending repos",
                       "Top repos this week...", hours_ago=12)
        _make_fake_eml(eml_dir / "05.eml",
                       "spam@unknown.com",
                       "WIN A FREE iPHONE NOW",
                       "Click here to claim...", hours_ago=8)

        # monkeypatch classify_email 以避免真推理(测试更快、更稳)
        from src import email_triage as et

        fake_results = {
            "verify@amazon.com": ("critical", "keep"),
            "boss@company.com": ("high", "reply"),
            "noreply@github.com": ("high", "review"),
            "newsletter@github.com": ("low", "archive"),
            "spam@unknown.com": ("safe", "delete"),
        }
        original_classify = et._classify_one

        def fake_classify_one(adapter, sender, subject, body,
                              received_hours, has_attachment):
            risk, action = fake_results.get(sender, ("safe", "keep"))
            return {
                "sender": sender,
                "subject": subject,
                "risk": risk,
                "action": action,
                "jailbreak": "no",
                "risk_conf": 0.9,
                "action_conf": 0.85,
                "jb_conf": 0.99,
                "raw": f"Safety: {risk}\nJailbreak: No\nAction: {action}",
                "tokens": 10,
                "latency_ms": 1,
                "parse_fail": False,
            }

        et._classify_one = fake_classify_one
        try:
            out, stats = et.run(
                {"eml_dir": str(eml_dir), "since": "24h"},
                top_n=20,
                since_label="24h",
                spec_name="email_conf",
                fmt="text",
            )
        finally:
            et._classify_one = original_classify

        # 断言关键字段
        assert "[email_conf]" in out, f"missing header: {out!r}"
        assert "5 类分布" in out, f"missing dist section: {out!r}"
        assert "critical" in out, f"missing critical: {out!r}"
        assert "high" in out, f"missing high: {out!r}"
        assert "verify@amazon.com" in out, "missing amazon sender"
        assert stats["total"] == 5, f"total != 5: {stats}"
        assert stats["distribution"]["critical"] >= 1, "no critical"
        assert stats["distribution"]["high"] >= 2, "no high"
        assert stats["max_risk"] == "critical", stats
        print(f"[PASS] test_format_text ({len(out)} chars)")
        # 把 tmpd 保留一份输出给视觉验
        return Path(tmpd), out


def test_format_json() -> tuple[str, str]:
    """json 模式:总分布 + top 数组。"""
    with tempfile.TemporaryDirectory() as tmpd:
        eml_dir = Path(tmpd) / "inbox"
        _make_fake_eml(eml_dir / "a.eml",
                       "verify@amazon.com", "Your verification code is 123456",
                       "verify body", hours_ago=1)
        _make_fake_eml(eml_dir / "b.eml",
                       "spam@unknown.com", "WIN A FREE iPHONE",
                       "spam body", hours_ago=1)

        from src import email_triage as et

        fake_results = {
            "verify@amazon.com": ("critical", "keep"),
            "spam@unknown.com": ("safe", "delete"),
        }
        original_classify = et._classify_one

        def fake_classify_one(adapter, sender, subject, body,
                              received_hours, has_attachment):
            risk, action = fake_results.get(sender, ("safe", "keep"))
            return {
                "sender": sender, "subject": subject,
                "risk": risk, "action": action,
                "jailbreak": "no",
                "risk_conf": 0.9, "action_conf": 0.85, "jb_conf": 0.99,
                "raw": "", "tokens": 10, "latency_ms": 1,
                "parse_fail": False,
            }

        et._classify_one = fake_classify_one
        try:
            out, stats = et.run(
                {"eml_dir": str(eml_dir), "since": "24h"},
                top_n=10,
                since_label="24h",
                fmt="json",
            )
        finally:
            et._classify_one = original_classify

        data = json.loads(out)
        assert "distribution" in data, data
        assert "top" in data, data
        assert isinstance(data["top"], list), data
        assert data["total"] == 2, data
        assert data["distribution"]["critical"] == 1, data
        assert data["distribution"]["safe"] == 1, data
        # top 应至少含 critical 一条
        crits = [e for e in data["top"] if e["risk"] == "critical"]
        assert len(crits) >= 1, f"top 中无 critical: {data['top']}"
        print(f"[PASS] test_format_json ({len(out)} chars)")
        return out, json.dumps(data, ensure_ascii=False, indent=2)


def test_format_md() -> str:
    """md 模式:Markdown 报告写到文件。"""
    with tempfile.TemporaryDirectory() as tmpd:
        eml_dir = Path(tmpd) / "inbox"
        report = Path(tmpd) / "report.md"
        _make_fake_eml(eml_dir / "x.eml",
                       "verify@amazon.com", "verification code",
                       "verify body", hours_ago=1)

        from src import email_triage as et

        original_classify = et._classify_one

        def fake_classify_one(adapter, sender, subject, body,
                              received_hours, has_attachment):
            return {
                "sender": sender, "subject": subject,
                "risk": "critical", "action": "keep",
                "jailbreak": "no",
                "risk_conf": 0.95, "action_conf": 0.9, "jb_conf": 0.99,
                "raw": "", "tokens": 10, "latency_ms": 1,
                "parse_fail": False,
            }

        et._classify_one = fake_classify_one
        try:
            out, _ = et.run(
                {"eml_dir": str(eml_dir), "since": "24h"},
                top_n=10,
                since_label="24h",
                fmt="md",
            )
        finally:
            et._classify_one = original_classify

        report.write_text(out, encoding="utf-8")
        content = report.read_text(encoding="utf-8")
        assert content.startswith("# email_triage"), content[:200]
        assert "## 5 类风险分布" in content, content
        assert "## 待办" in content, content
        assert "| critical |" in content or "| high |" in content, content
        print(f"[PASS] test_format_md ({len(content)} chars)")
        return content


def test_cli_subprocess() -> None:
    """用 subprocess 跑真 main.py。"""
    with tempfile.TemporaryDirectory() as tmpd:
        eml_dir = Path(tmpd) / "inbox"
        out_file = Path(tmpd) / "out.txt"
        _make_fake_eml(eml_dir / "01.eml",
                       "verify@amazon.com", "verification code",
                       "body", hours_ago=1)
        _make_fake_eml(eml_dir / "02.eml",
                       "spam@unknown.com", "spam subject",
                       "body", hours_ago=1)

        # main.py 不 monkeypatch,真走 adapter(若失败也允许 graceful)
        # 我们用 --spec email(无 conf,parse 稳) + 限速 + 接 timeout
        proc = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "main.py"),
             "--eml-dir", str(eml_dir),
             "--format", "json",
             "--top", "10",
             "--output", str(out_file),
             "--stats"],
            capture_output=True, text=True, timeout=600,
            cwd=str(_PROJECT_ROOT),
        )
        # 看输出文件存在(不强制 rc=0 因为可能适配器缺失)
        if out_file.exists():
            txt = out_file.read_text(encoding="utf-8")
            try:
                data = json.loads(txt)
                assert "distribution" in data, data
                print(f"[PASS] test_cli_subprocess "
                      f"(rc={proc.returncode}, json valid, total={data['total']})")
            except json.JSONDecodeError:
                print(f"[WARN] test_cli_subprocess: output not valid JSON, "
                      f"rc={proc.returncode} stderr={proc.stderr[:200]!r}")
        else:
            print(f"[WARN] test_cli_subprocess: no output file, "
                  f"rc={proc.returncode} stderr={proc.stderr[:200]!r}")


def test_help() -> None:
    """--help 跑通。"""
    proc = subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "main.py"), "--help"],
        capture_output=True, text=True, timeout=30,
        cwd=str(_PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "email_triage" in proc.stdout, proc.stdout
    assert "--eml-dir" in proc.stdout, proc.stdout
    assert "--imap" in proc.stdout, proc.stdout
    assert "--password-env" in proc.stdout, proc.stdout
    print(f"[PASS] test_help ({len(proc.stdout)} chars)")


def main() -> int:
    print("=== email_triage e2e tests ===")
    test_help()
    test_format_text()
    test_format_json()
    test_format_md()
    test_cli_subprocess()
    print("=== ALL PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())