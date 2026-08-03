"""ops_dashboard.py — 本地运营观测页生成器(通天尺规主站 + 开发项目)

在本地 Windows 跑:经 SSH 到 VPS 拉两类数据 —
  (1) 主站自研计数:PG `events` 表(ts, page, work_id) — 通天尺规/Babelspan 访问
  (2) 开发项目状态:smp-server(5223)/ xftp-server(8443) 监听 + 连接数(系统指标,非 Web)

生成一个自包含 HTML(内嵌 Chart.js CDN + 数据),双击即可在浏览器看分项目报表。
不动主站任何计数逻辑,纯只读。

跑法:
  python ops_dashboard.py                 # 生成 ops_dashboard.html 并尝试打开
  python ops_dashboard.py --no-open       # 只生成不打开

依赖:ssh 免密已配(ed25519)。无需 pip 安装 psycopg2 —— 直接调远端 psql 出 CSV。
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VPS_HOST = "192.220.14.165"
VPS_PORT = "49108"
VPS_USER = "root"
SSH_KEY = str(Path.home() / ".ssh" / "id_ed25519")
DB = "rubriclab"
OUT_HTML = Path(__file__).resolve().parent / "ops_dashboard.html"

SSH_BASE = [
    "ssh", "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=15",
    "-o", "BatchMode=yes",
    "-p", VPS_PORT, f"{VPS_USER}@{VPS_HOST}",
]


def _ssh(remote_cmd: str, timeout: int = 30) -> str:
    """在 VPS 执行命令,返回 stdout。失败抛 RuntimeError 带 stderr。"""
    p = subprocess.run(
        SSH_BASE + [remote_cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"SSH 失败(rc={p.returncode}):{p.stderr.strip()[:400]}")
    return p.stdout


def fetch_events() -> list[dict]:
    """拉 events 全量(268 行级,直接全拉)。返回 [{ts, page, work_id}]。"""
    sql = (
        "COPY (SELECT ts, COALESCE(page,''), COALESCE(work_id,'') "
        "FROM events ORDER BY ts) TO STDOUT WITH CSV HEADER"
    )
    out = _ssh(f"sudo -u postgres psql -d {DB} -c \"{sql}\"")
    rows = []
    for i, line in enumerate(out.splitlines()):
        if i == 0 or not line.strip():
            continue  # header
        # CSV:ts,page,work_id(page 可能含逗号,简单按首两逗号切)
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        ts, page, work_id = parts[0], parts[1], parts[2].strip().strip('"')
        rows.append({"ts": ts, "page": page, "work_id": work_id})
    return rows


def fetch_smp_status() -> dict:
    """smp/xftp 监听 + 已建立连接数(系统指标,非 Web 计数)。"""
    out = _ssh(
        "ss -tlnp | grep -E '5223|8443' || true; "
        "echo '---ESTAB---'; "
        "ss -tnp state established '( sport = :5223 or sport = :8443 )' 2>/dev/null | tail -n +2 | wc -l; "
        "echo '---SERVICES---'; "
        "systemctl is-active smp-server xftp-server 2>/dev/null || true"
    )
    listen_lines, estab, services = [], "0", ""
    section = "listen"
    for line in out.splitlines():
        if line.startswith("---ESTAB---"):
            section = "estab"; continue
        if line.startswith("---SERVICES---"):
            section = "svc"; continue
        if section == "listen" and line.strip():
            listen_lines.append(line.strip())
        elif section == "estab" and line.strip().isdigit():
            estab = line.strip()
        elif section == "svc":
            services += line.strip() + " "
    return {
        "listen": listen_lines,
        "established": int(estab),
        "services": services.strip(),
    }


def analyze(events: list[dict]) -> dict:
    """主站访问分析:按天趋势、按 page top、按 work top。"""
    by_day = Counter()
    by_page = Counter()
    by_work = Counter()
    for e in events:
        day = e["ts"][:10]  # YYYY-MM-DD
        by_day[day] += 1
        by_page[e["page"] or "(空)"] += 1
        if e["work_id"]:
            by_work[e["work_id"]] += 1
    return {
        "total": len(events),
        "by_day": dict(sorted(by_day.items())),
        "top_pages": by_page.most_common(15),
        "top_works": by_work.most_common(15),
        "first_ts": events[0]["ts"] if events else None,
        "last_ts": events[-1]["ts"] if events else None,
    }


def render(stats: dict, smp: dict) -> str:
    days = list(stats["by_day"].keys())
    day_counts = list(stats["by_day"].values())
    top_pages_labels = [p for p, _ in stats["top_pages"]]
    top_pages_counts = [c for _, c in stats["top_pages"]]
    top_works_labels = [w for w, _ in stats["top_works"]]
    top_works_counts = [c for _, c in stats["top_works"]]
    gen_time = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    listen_html = "".join(
        f"<li><code>{html.escape(l)}</code></li>" for l in smp["listen"]
    ) or "<li>(无监听输出)</li>"

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>运营观测 — 通天尺规 + 开发项目</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body{{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:0;background:#0f1115;color:#e6e6e6}}
  header{{padding:18px 24px;background:#171a21;border-bottom:1px solid #262b36}}
  header h1{{margin:0;font-size:20px}}
  header .sub{{color:#8a93a6;font-size:12px;margin-top:4px}}
  .tabs{{display:flex;gap:4px;padding:0 24px;background:#171a21}}
  .tab{{padding:10px 16px;cursor:pointer;border:none;background:transparent;color:#8a93a6;
        border-bottom:2px solid transparent;font-size:14px}}
  .tab.active{{color:#fff;border-bottom-color:#4f8cff}}
  .panel{{display:none;padding:24px}}
  .panel.active{{display:block}}
  .cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}}
  .card{{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:16px 20px;min-width:160px}}
  .card .num{{font-size:28px;font-weight:700}}
  .card .lbl{{color:#8a93a6;font-size:12px;margin-top:4px}}
  .chart-box{{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:16px;margin-bottom:20px}}
  .chart-box h3{{margin:0 0 12px;font-size:15px}}
  canvas{{max-height:320px}}
  .note{{background:#1c1305;border:1px solid #4a3a12;color:#e8c56a;border-radius:8px;
         padding:10px 14px;font-size:13px;margin-bottom:16px}}
  code{{background:#0b0d11;padding:2px 6px;border-radius:4px;font-size:12px}}
  ul{{line-height:1.9}}
</style>
</head>
<body>
<header>
  <h1>运营观测 · 通天尺规(Babelspan)+ 开发项目</h1>
  <div class="sub">本地生成 · {html.escape(gen_time)} · 数据源:VPS PG events 表(只读)+ smp/xftp 系统指标 · 主站计数逻辑未改动</div>
</header>
<div class="tabs">
  <button class="tab active" data-p="main">主站(通天尺规)</button>
  <button class="tab" data-p="dev">开发项目(smp/xftp)</button>
  <button class="tab" data-p="about">口径说明</button>
</div>

<div class="panel active" id="p-main">
  <div class="cards">
    <div class="card"><div class="num">{stats["total"]}</div><div class="lbl">累计访问事件</div></div>
    <div class="card"><div class="num">{len(stats["by_day"])}</div><div class="lbl">有访问的天数</div></div>
    <div class="card"><div class="num">{len(stats["top_works"])}</div><div class="lbl">被访问的作品数</div></div>
  </div>
  <div class="note">口径:主站自研计数为<strong>隐私友好设计</strong>——只记 <code>ts + page + work_id</code>,
  <strong>不存 IP / UA / cookie</strong>,故无独立访客数(UV)、无地域、无来源,只有页面/作品维度的访问次数。</div>
  <div class="chart-box"><h3>每日访问趋势</h3><canvas id="c-day"></canvas></div>
  <div class="chart-box"><h3>页面访问 Top</h3><canvas id="c-page"></canvas></div>
  <div class="chart-box"><h3>作品访问 Top</h3><canvas id="c-work"></canvas></div>
</div>

<div class="panel" id="p-dev">
  <div class="cards">
    <div class="card"><div class="num">{smp["established"]}</div><div class="lbl">smp/xftp 当前已建立连接</div></div>
    <div class="card"><div class="num" style="font-size:16px">{html.escape(smp["services"])}</div><div class="lbl">systemd 服务状态</div></div>
  </div>
  <div class="note">开发项目(smp 5223 / xftp 8443)是<strong>裸 TCP,不经 Cloudflare</strong>,
  CF 面板永远看不到这部分。此处只显示<strong>系统级监听/连接数</strong>,非 Web 访问计数。
  将来 <code>dev.babelspan.com</code> Web 上线后,其访问计数会按子域单独接入此页。</div>
  <div class="chart-box"><h3>当前监听(5223 / 8443)</h3><ul>{listen_html}</ul></div>
</div>

<div class="panel" id="p-about">
  <div class="chart-box"><h3>数据口径与项目隔离</h3>
  <ul>
    <li><strong>主站(通天尺规 / Babelspan)</strong>:书籍评价导航平台,与开发项目<strong>完全无关</strong>。计数 = PG <code>events</code> 表,隐私友好(无 IP/UA)。</li>
    <li><strong>rubriclab</strong> = 已废弃的旧项目名,仅存在于 VPS 历史目录/进程名,不代表当前品牌。</li>
    <li><strong>隔离方式</strong>:按<strong>子域</strong>切分。主站不动;开发项目用 <code>dev./smp./xftp.</code> 子域,另起计数。</li>
    <li><strong>Cloudflare</strong>:主站 <code>www.babelspan.com</code> 走 Let's Encrypt 直连,<strong>不经 CF 代理</strong>,故 CF 面板对主站无完整数据,只能依赖服务器端自研计数(即本页数据源)。</li>
    <li><strong>smp/xftp</strong>:裸 TCP 灰云,CF 不可见,只有系统指标。</li>
    <li>本页由 <code>ops_dashboard.py</code> 本地生成,SSH 只读拉取,不改 VPS 任何配置。</li>
  </ul></div>
</div>

<script>
const D = {{
  days: {json.dumps(days)}, dayCounts: {json.dumps(day_counts)},
  pageLabels: {json.dumps(top_pages_labels)}, pageCounts: {json.dumps(top_pages_counts)},
  workLabels: {json.dumps(top_works_labels)}, workCounts: {json.dumps(top_works_counts)},
}};
const gridClr = '#262b36', tickClr = '#8a93a6';
function mk(id, type, labels, data, label, horizontal) {{
  new Chart(document.getElementById(id), {{
    type: type,
    data: {{ labels, datasets: [{{ label, data, backgroundColor:'#4f8cff',
           borderColor:'#4f8cff', fill:type==='line', tension:0.3 }}] }},
    options: {{
      indexAxis: horizontal ? 'y' : 'x',
      plugins: {{ legend: {{ display:false }} }},
      scales: {{
        x: {{ grid:{{color:gridClr}}, ticks:{{color:tickClr}} }},
        y: {{ grid:{{color:gridClr}}, ticks:{{color:tickClr,precision:0}} }},
      }},
    }},
  }});
}}
mk('c-day','line', D.days, D.dayCounts, '访问');
mk('c-page','bar', D.pageLabels, D.pageCounts, '次数', true);
mk('c-work','bar', D.workLabels, D.workCounts, '次数', true);

document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('p-'+t.dataset.p).classList.add('active');
}}));
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    print("[1/3] SSH 拉主站 events ...", flush=True)
    try:
        events = fetch_events()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL 拉 events:{e}", file=sys.stderr)
        return 1
    print(f"      {len(events)} 行", flush=True)

    print("[2/3] SSH 拉 smp/xftp 状态 ...", flush=True)
    try:
        smp = fetch_smp_status()
    except Exception as e:  # noqa: BLE001
        print(f"WARN smp 状态拉取失败:{e}(继续,标 N/A)", file=sys.stderr)
        smp = {"listen": [], "established": 0, "services": "N/A"}

    print("[3/3] 生成 HTML ...", flush=True)
    stats = analyze(events)
    OUT_HTML.write_text(render(stats, smp), encoding="utf-8")
    print(f"      → {OUT_HTML}", flush=True)

    if not args.no_open:
        webbrowser.open(OUT_HTML.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
