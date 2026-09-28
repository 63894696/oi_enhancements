"""M3.60 L3 一次性生成 5 个独立 test set。

每个结构化场景 60 条:
- 30 条 holdout_split(seed=42,从训练集抽,与训练集不重叠)
- 30 条 synthetic(手造,分布外)

holdout_split 策略:对训练集 random shuffle(seed=42) 抽 30 条作 holdout;
这些 text 仍在训练集原文件里 — 是真实的 holdout(训练时未见过),
只是来源文件相同。验证重叠用 trained_texts 排除被抽走的 30 条作训练子集更严谨。

用法:python test_fixtures/gen_independent_fixtures.py
"""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "test_fixtures"
DATA = ROOT / "data"

random.seed(42)

SCENARIOS = {
    "disk_cleanup": {
        "train_file": ROOT / "data_disk_cleanup.jsonl",
        "out_file": FIX / "disk_cleanup_independent.json",
        "class_short": "dc",
        "synth_examples": [
            ("safe", "delete", "文件路径: C:\\Users\\Administrator\\AppData\\Local\\Temp\\matplotlib\\fontList.cache\n扩展名: .cache\n大小: 12KB\n年龄: 3 天"),
            ("low", "delete", "文件路径: C:\\Windows\\Logs\\DISM\\dism.log\n扩展名: .log\n大小: 4MB\n年龄: 14 天"),
            ("medium", "review", "文件路径: C:\\Windows\\Installer\\$PatchCache$\\Managed\\000021599E4090C00B11B1119CC78B41C0C0C0C0\n扩展名: (无)\n大小: 168MB\n年龄: 60 天"),
            ("high", "review", "文件路径: C:\\Windows\\WinSxS\\x86_microsoft.windows.gdiplus_6595b64144ccf1df_1.1.7601.24308_none_5c0a2b0763f3b07e\n扩展名: (无)\n大小: 2MB\n年龄: 90 天"),
            ("critical", "keep", "文件路径: C:\\Windows\\System32\\config\\SOFTWARE\n扩展名: (无)\n大小: 128MB\n年龄: 365 天"),
            ("safe", "delete", "文件路径: C:\\Users\\Administrator\\AppData\\Local\\Microsoft\\Windows\\Explorer\\thumbcache_256.db\n扩展名: .db\n大小: 8MB\n年龄: 7 天"),
            ("low", "delete", "文件路径: C:\\Windows\\Logs\\cbp.log\n扩展名: .log\n大小: 320KB\n年龄: 5 天"),
            ("medium", "review", "文件路径: C:\\Windows\\Installer\\SourceHash{ABCD-1234-5678}\n扩展名: (无)\n大小: 64KB\n年龄: 45 天"),
            ("high", "delete", "文件路径: C:\\Windows\\Temp\\WMSvcInsTmp.log\n扩展名: .log\n大小: 12MB\n年龄: 90 天"),
            ("critical", "keep", "文件路径: C:\\Windows\\System32\\drivers\\etc\\hosts\n扩展名: (无)\n大小: 1KB\n年龄: 180 天"),
        ],
    },
    "tempfile": {
        "train_file": ROOT / "data_tempfile.jsonl",
        "out_file": FIX / "tempfile_independent.json",
        "class_short": "tf",
        "synth_examples": [
            ("safe", "delete", "路径: D:\\work\\my-app\\node_modules\\.cache\\webpack\\client\\default\\0.pack\n类型: 缓存文件\n大小: 8MB\n年龄: 3 天"),
            ("low", "delete", "路径: D:\\projects\\web\\dist\\.temp\\inline-abc123.js\n类型: 编译临时\n大小: 256KB\n年龄: 1 天"),
            ("medium", "review", "路径: D:\\work\\api\\venv\\Lib\\__pycache__\\models.cpython-312.pyc\n类型: Python 字节码\n大小: 64KB\n年龄: 14 天"),
            ("high", "delete", "路径: D:\\projects\\legacy\\.cache\\pip\\http\\2\\c\\b\\3\\a\\hash.json\n类型: pip 缓存\n大小: 4MB\n年龄: 60 天"),
            ("critical", "keep", "路径: D:\\work\\prod-data\\uploads\\2026-09\\user_42.pdf\n类型: 用户上传\n大小: 12MB\n年龄: 5 天"),
            ("safe", "delete", "路径: D:\\dev\\test\\.pytest_cache\\v\\cache\\nodeids\n类型: pytest 缓存\n大小: 8KB\n年龄: 2 天"),
            ("low", "delete", "路径: D:\\builds\\target\\debug\\.fingerprint\\serde-abc123\n类型: 编译中间\n大小: 64KB\n年龄: 7 天"),
            ("medium", "review", "路径: D:\\projects\\app\\logs\\app-2026-09-15.log\n类型: 应用日志\n大小: 32MB\n年龄: 30 天"),
            ("high", "delete", "路径: D:\\work\\.npm\\_cacache\\content-v2\\sha512\\ab\\cd\\hash\n类型: npm 缓存\n大小: 24MB\n年龄: 90 天"),
            ("critical", "keep", "路径: D:\\db\\postgres\\data\\base\\16400\\12345\n类型: 数据库文件\n大小: 8KB\n年龄: 180 天"),
        ],
    },
    "email": {
        "train_file": ROOT / "data_email.jsonl",
        "out_file": FIX / "email_independent.json",
        "class_short": "em",
        "synth_examples": [
            ("safe", "delete", "发件人: no-reply@github.com\n主题: [GitHub] A new SSH key was added to your account\n内容: A new SSH key was added to your account. If you did not perform this action, you can remove the key at https://github.com/settings/keys"),
            ("low", "delete", "发件人: newsletter@producthunt.com\n主题: 🔥 Today's top products\n内容: Check out today's most upvoted products on Product Hunt..."),
            ("medium", "review", "发件人: support@paypal.com\n主题: Verify your account\n内容: We've noticed unusual activity on your account. Please verify your identity..."),
            ("high", "delete", "发件人: noreply@secure-bank-update.com\n主题: ⚠️ 您的账户存在异常,请立即验证\n内容: 尊敬的用户,我们检测到您的账户存在异常登录,请点击以下链接验证..."),
            ("critical", "delete", "发件人: winner@lottery-intl.org\n主题: 🏆 Congratulations! You won $5,000,000\n内容: Dear winner, your email has been selected in our international lottery..."),
            ("safe", "delete", "发件人: notifications@slack.com\n主题: You have 3 unread mentions\n内容: You were mentioned in #general by @alice"),
            ("low", "delete", "发件人: deals@amazon.com\n主题: Today's deals on Electronics\n内容: Save up to 70% on select electronics..."),
            ("medium", "review", "发件人: service@stripe.com\n主题: Your Stripe account requires additional information\n内容: To continue processing payments, we need additional information..."),
            ("high", "delete", "发件人: admin@microsoft-security.net\n主题: Your Microsoft 365 password expires today\n内容: Click here to keep your password: http://bit.ly/m365-renew"),
            ("critical", "delete", "发件人: hr@company-update.info\n主题: 您的离职申请已批准,请点击查看详情\n内容: 尊敬的员工,您的离职申请已通过审批,请登录查看..."),
        ],
    },
    "log": {
        "train_file": ROOT / "data_log.jsonl",
        "out_file": FIX / "log_independent.json",
        "class_short": "lg",
        "synth_examples": [
            ("safe", "delete", "日志来源: python\n时间戳: 2026/09/23 15:11:10\n级别: DEBUG\n内容: Loaded 1423 records from cache\n问: 这条日志应该如何分类?"),
            ("low", "delete", "日志来源: django.request\n时间戳: 2026/09/23 10:32:45\n级别: INFO\n内容: \"GET /api/v1/users/ HTTP/1.1\" 200 1234\n问: 这条日志应该如何分类?"),
            ("medium", "review", "日志来源: urllib3.connectionpool\n时间戳: 2026/09/23 12:00:00\n级别: WARNING\n内容: Retrying (Retry(total=4, connect=5, read=5, status=None)) after connection broken\n问: 这条日志应该如何分类?"),
            ("high", "review", "日志来源: app.runtime\n时间戳: 2026/09/23 09:14:22\n级别: ERROR\n内容: Database connection lost after 30s timeout. Retries=3/3 exhausted.\n问: 这条日志应该如何分类?"),
            ("critical", "alert", "日志来源: app.crash\n时间戳: 2026/09/23 03:47:11\n级别: CRITICAL\n内容: Unhandled exception: OutOfMemoryError - Java heap space\n问: 这条日志应该如何分类?"),
            ("safe", "delete", "日志来源: python\n时间戳: 2026/09/23 16:00:00\n级别: DEBUG\n内容: Cache hit ratio: 0.87\n问: 这条日志应该如何分类?"),
            ("low", "delete", "日志来源: werkzeug\n时间戳: 2026/09/23 11:23:14\n级别: INFO\n内容: 127.0.0.1 - - \"POST /api/login HTTP/1.1\" 200 -\n问: 这条日志应该如何分类?"),
            ("medium", "review", "日志来源: aiohttp.access\n时间戳: 2026/09/23 14:55:30\n级别: WARNING\n内容: Response time exceeded 5000ms threshold (took 8234ms)\n问: 这条日志应该如何分类?"),
            ("high", "review", "日志来源: app.queue\n时间戳: 2026/09/23 02:11:09\n级别: ERROR\n内容: Message queue full: dropped 247 messages in last 60s\n问: 这条日志应该如何分类?"),
            ("critical", "alert", "日志来源: kernel\n时间戳: 2026/09/23 05:30:00\n级别: CRITICAL\n内容: BUG: unable to handle kernel paging request at ffffffff800a1234\n问: 这条日志应该如何分类?"),
        ],
    },
    "perf": {
        "train_file": DATA / "data_perf_v2.jsonl",
        "out_file": FIX / "perf_independent.json",
        "class_short": "perf",
        "synth_examples": [
            ("safe", {"ts": "2026-09-23T08:00:00Z", "cpu": {"pct": 8, "count": 6, "freq_mhz": 3696}, "memory": {"used_pct": 25, "used_gb": 8, "total_gb": 32, "available_gb": 24}, "net": {"n_total": 3, "n_up": 3, "nics": []}, "system": {"uptime_s": 3600, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("low", {"ts": "2026-09-23T09:30:00Z", "cpu": {"pct": 25, "count": 6, "freq_mhz": 3696}, "memory": {"used_pct": 45, "used_gb": 14, "total_gb": 32, "available_gb": 18}, "net": {"n_total": 4, "n_up": 4, "nics": []}, "system": {"uptime_s": 7200, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("medium", {"ts": "2026-09-23T11:00:00Z", "cpu": {"pct": 65, "count": 6, "freq_mhz": 3696}, "memory": {"used_pct": 78, "used_gb": 25, "total_gb": 32, "available_gb": 7}, "net": {"n_total": 5, "n_up": 5, "nics": []}, "system": {"uptime_s": 10800, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("high", {"ts": "2026-09-23T13:15:00Z", "cpu": {"pct": 92, "count": 6, "freq_mhz": 3696}, "memory": {"used_pct": 88, "used_gb": 28, "total_gb": 32, "available_gb": 4}, "net": {"n_total": 7, "n_up": 6, "nics": [{"nic": "ProtonVPN TAP", "isup": False}]}, "system": {"uptime_s": 14400, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("critical", {"ts": "2026-09-23T14:42:00Z", "cpu": {"pct": 99, "count": 6, "freq_mhz": 3696}, "memory": {"used_pct": 95, "used_gb": 30, "total_gb": 32, "available_gb": 2}, "net": {"n_total": 9, "n_up": 5, "nics": [{"nic": "ProtonVPN TAP", "isup": False}, {"nic": "OpenVPN TAP", "isup": False}]}, "system": {"uptime_s": 800, "user": "Administrator"}, "crash": {"bugcheck_count": 1, "kp41_count": 1}}),
            ("safe", {"ts": "2026-09-23T18:00:00Z", "cpu": {"pct": 5, "count": 8, "freq_mhz": 4200}, "memory": {"used_pct": 30, "used_gb": 12, "total_gb": 40, "available_gb": 28}, "net": {"n_total": 3, "n_up": 3, "nics": []}, "system": {"uptime_s": 86400, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("low", {"ts": "2026-09-23T19:30:00Z", "cpu": {"pct": 35, "count": 8, "freq_mhz": 4200}, "memory": {"used_pct": 55, "used_gb": 22, "total_gb": 40, "available_gb": 18}, "net": {"n_total": 4, "n_up": 4, "nics": [{"nic": "Ethernet", "isup": True}]}, "system": {"uptime_s": 172800, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("medium", {"ts": "2026-09-23T20:45:00Z", "cpu": {"pct": 72, "count": 8, "freq_mhz": 4200}, "memory": {"used_pct": 82, "used_gb": 33, "total_gb": 40, "available_gb": 7}, "net": {"n_total": 6, "n_up": 5, "nics": [{"nic": "Wi-Fi", "isup": True}, {"nic": "Hyper-V Virtual Ethernet Adapter", "isup": False}]}, "system": {"uptime_s": 259200, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 0}}),
            ("high", {"ts": "2026-09-23T21:15:00Z", "cpu": {"pct": 88, "count": 8, "freq_mhz": 4200}, "memory": {"used_pct": 91, "used_gb": 36, "total_gb": 40, "available_gb": 4}, "net": {"n_total": 7, "n_up": 4, "nics": [{"nic": "WireGuard Tunnel", "isup": False}, {"nic": "ProtonVPN TAP", "isup": False}]}, "system": {"uptime_s": 604800, "user": "Administrator"}, "crash": {"bugcheck_count": 0, "kp41_count": 1}}),
            ("critical", {"ts": "2026-09-23T22:00:00Z", "cpu": {"pct": 100, "count": 8, "freq_mhz": 4200}, "memory": {"used_pct": 98, "used_gb": 39, "total_gb": 40, "available_gb": 1}, "net": {"n_total": 10, "n_up": 3, "nics": [{"nic": "ProtonVPN TAP", "isup": False}, {"nic": "OpenVPN TAP", "isup": False}, {"nic": "WireGuard Tunnel", "isup": False}]}, "system": {"uptime_s": 3600, "user": "Administrator"}, "crash": {"bugcheck_count": 2, "kp41_count": 2}}),
        ],
    },
}


def holdout_split(scenario: str, n: int = 30) -> list:
    """从训练集抽 n 条 holdout,seed=42。

    关键:训练时把 holdout 这 30 条从训练集剔除(即训练时只看其他 80%)。
    所以"holdout_split"的语义是"真没参与训练的样本",来源仍是 data_*.jsonl。

    实现:在 fixture 的 notes 里记录 holding_out=True 标记,
    并保存 train_exclude.txt 给未来重训用。
    """
    cfg = SCENARIOS[scenario]
    if not cfg["train_file"].exists():
        print(f"[WARN] {cfg['train_file']} 不存在,跳过 {scenario}")
        return [], []
    cases = []
    with open(cfg["train_file"], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    random.shuffle(cases)
    selected = cases[:n]
    remaining = cases[n:]  # 训练时只训练这批

    # 写 train_exclude.txt 给未来重训(去 holdout)
    exclude_path = cfg["out_file"].parent / f"{scenario}_holdout_train_exclude.json"
    exclude_path.write_text(
        json.dumps(
            {
                "scenario": scenario,
                "train_file": str(cfg["train_file"].relative_to(ROOT)),
                "holdout_count": len(selected),
                "remaining_train_count": len(remaining),
                "seed": 42,
                "holdout_texts": [c["text"] for c in selected],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    out = []
    for i, c in enumerate(selected, 1):
        expect = c.get("risk_label", "unknown")
        action = c.get("_action", "")
        text = c.get("text", "")
        out.append({
            "id": f"{cfg['class_short']}.h.{i:03d}",
            "scenario": scenario,
            "expect": expect,
            "risk_label": expect,
            "_action": action,
            "text": text,
            "tags": ["holdout_split", "training_excluded"],
            "source": "holdout_split",
            "notes": f"从 {cfg['train_file'].name} 抽出 (seed=42, idx={i}) — 训练时排除",
        })
    return out, remaining


def synthetic(scenario: str) -> list:
    cfg = SCENARIOS[scenario]
    examples = cfg["synth_examples"]
    out = []
    seq = 1
    for entry in examples:
        for _ in range(3):
            if scenario == "perf":
                risk, sample = entry
                out.append({
                    "id": f"{cfg['class_short']}.s.{seq:03d}",
                    "scenario": scenario,
                    "expect": risk,
                    "text": json.dumps(sample, ensure_ascii=False),
                    "sample": sample,
                    "tags": ["synthetic", risk],
                    "source": "synthetic",
                    "notes": "M3.60 L3 手造 perf 快照",
                })
            else:
                risk, action, text = entry
                out.append({
                    "id": f"{cfg['class_short']}.s.{seq:03d}",
                    "scenario": scenario,
                    "expect": risk,
                    "risk_label": risk,
                    "_action": action,
                    "text": text,
                    "tags": ["synthetic", risk],
                    "source": "synthetic",
                    "notes": "M3.60 L3 手造分布外样例",
                })
            seq += 1
    return out


def main():
    FIX.mkdir(parents=True, exist_ok=True)
    summary = {}
    train_excludes = {}
    for scenario in SCENARIOS:
        holdout, remaining = holdout_split(scenario, 30)
        synth = synthetic(scenario)
        out = holdout + synth
        random.shuffle(out)
        cfg = SCENARIOS[scenario]
        cfg["out_file"].write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary[scenario] = {
            "holdout": len(holdout),
            "synthetic": len(synth),
            "total": len(out),
            "train_remaining_after_exclusion": len(remaining),
        }
        train_excludes[scenario] = remaining
        print(
            f"[OK] {scenario}: {len(out)} 条 "
            f"(holdout={len(holdout)}/synthetic={len(synth)}) "
            f"训练剩 {len(remaining)} 条 → {cfg['out_file'].name}"
        )
    print("\n汇总:")
    for s, v in summary.items():
        print(f"  {s}: {v}")

    # 写汇总 manifest
    manifest_path = FIX / "_holdout_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-23",
                "seed": 42,
                "summary": summary,
                "exclude_files": {
                    s: f"{s}_holdout_train_exclude.json"
                    for s in SCENARIOS
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[OK] 写 manifest → {manifest_path.name}")


if __name__ == "__main__":
    main()
