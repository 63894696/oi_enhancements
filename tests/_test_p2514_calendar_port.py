"""P2.5+14 独立日历端口 (2026-09-22)

覆盖:
  S1: companion/music/port_config.py DEFAULT_CALENDAR_PORT = 18803
  S2: companion/music/port_config.py quick_smoke 含 DEFAULT_CALENDAR_PORT + read_calendar
  S3: companion/music/port_config.py resolve_start_port('calendar', env, cli, default) 可调
  S4: companion/music/port_config.py notify_port_changed('calendar', old, new) 可调
  S5: companion/music/port_config.py pick_free_port(DEFAULT_CALENDAR_PORT) 可调
  S6: prisiragent-shell/port_config.js DEFAULT_CALENDAR_PORT = 18803
  S7: prisiragent-shell/port_config.js readCalendarPort 函数 + 模块导出
  S8: prisiragent-shell/port_config.js 注释提到 calendar 路径(实际 /prisIragent/calendar)
  S9: prisIragent_web.py CalendarHandler 类定义 + do_GET / do_POST / do_OPTIONS
  S10: prisIragent_web.py CalendarHandler 拒绝非日历路径(404)
  S11: prisIragent_web.py CalendarHandler /__calendar_ready 健康端点
  S12: prisIragent_web.py CalendarHandler 含 _serve_calendar_static 方法
  S13: prisIragent_web.py --calendar-port CLI flag 在 main() argparse 里
  S14: prisIragent_web.py _CONFIGURED_CALENDAR_PORT / _REAL_CALENDAR_PORT 全局
  S15: prisIragent_web.py /api/port_status 返 calendar 字段(configured/actual/changed/reason/enabled)
  S16: prisIragent_web.py main() 双端口 bootstrap(sentinel PRISIR_CALENDAR_READY)
  S17: prisIragent_web.py 日历端口失败兜底(OSError → fallback 主 web 端口)
  S18: prisiragent-tauri/src-tauri/src/port_config.rs DEFAULT_CALENDAR_PORT = 18803
  S19: prisiragent-tauri/src-tauri/src/port_config.rs read_calendar_port 函数
  S20: prisiragent-tauri/src-tauri/src/port_config.rs 默认值测试覆盖 DEFAULT_CALENDAR_PORT
  S21: py_compile prisIragent_web.py OK
  S22: port_config.py smoke 跑通(DEFAULT_CALENDAR_PORT = 18803)
"""
import os, re, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT_CFG_PY = os.path.join(ROOT, 'companion', 'music', 'port_config.py')
PORT_CFG_JS = os.path.join(ROOT, 'prisiragent-shell', 'port_config.js')
WEB_PY      = os.path.join(ROOT, 'prisIragent_web.py')
PORT_CFG_RS = os.path.join(ROOT, 'prisiragent-tauri', 'src-tauri', 'src', 'port_config.rs')

def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()

def check(name, ok, detail=''):
    mark = '✓' if ok else '✗'
    print(f"  [{mark}] {name}{(': ' + detail) if detail else ''}")
    return 1 if ok else 0

def main():
    py_src = read(PORT_CFG_PY)
    js_src = read(PORT_CFG_JS)
    web_src = read(WEB_PY)
    rs_src = read(PORT_CFG_RS)
    print("=" * 60)
    print("P2.5+14 独立日历端口 静态扫")
    print("=" * 60)
    passes = 0; total = 0

    # ─── S1: port_config.py DEFAULT_CALENDAR_PORT = 18803 ───
    print("\n[S1] port_config.py DEFAULT_CALENDAR_PORT = 18803")
    total += 1; passes += check("常量定义",
        re.search(r'^DEFAULT_CALENDAR_PORT\s*=\s*18803\b', py_src, re.MULTILINE) is not None)
    total += 1; passes += check("注释说明 18803 = web + 1",
        'web + 1' in py_src or 'web+1' in py_src)
    total += 1; passes += check("注释提到 P2.5+14",
        'P2.5+14' in py_src)

    # ─── S2: port_config.py quick_smoke 含 DEFAULT_CALENDAR_PORT + read_calendar ───
    print("\n[S2] port_config.py quick_smoke 包含日历字段")
    qs_section = py_src.split('def quick_smoke')[1].split('if __name__')[0] if 'def quick_smoke' in py_src else ''
    total += 1; passes += check("DEFAULT_CALENDAR_PORT",
        'DEFAULT_CALENDAR_PORT' in qs_section)
    total += 1; passes += check("read_calendar",
        'read_calendar' in qs_section)

    # ─── S3: resolve_start_port 可调 ───
    print("\n[S3] port_config.py resolve_start_port('calendar', ...) 可调")
    total += 1; passes += check("resolve_start_port 函数存在",
        'def resolve_start_port(name' in py_src)

    # ─── S4: notify_port_changed 可调 ───
    print("\n[S4] port_config.py notify_port_changed('calendar', ...) 可调")
    total += 1; passes += check("notify_port_changed 函数存在",
        'def notify_port_changed(' in py_src)

    # ─── S5: pick_free_port 可调 ───
    print("\n[S5] port_config.py pick_free_port 可调")
    total += 1; passes += check("pick_free_port 函数存在",
        'def pick_free_port(' in py_src)

    # ─── S6: port_config.js DEFAULT_CALENDAR_PORT = 18803 ───
    print("\n[S6] port_config.js DEFAULT_CALENDAR_PORT = 18803")
    total += 1; passes += check("常量定义",
        re.search(r'const\s+DEFAULT_CALENDAR_PORT\s*=\s*18803\b', js_src) is not None)
    total += 1; passes += check("注释说明 P2.5+14",
        'P2.5+14' in js_src)

    # ─── S7: port_config.js readCalendarPort ───
    print("\n[S7] port_config.js readCalendarPort")
    total += 1; passes += check("函数定义",
        'const readCalendarPort =' in js_src)
    total += 1; passes += check("readWinreg('calendar')",
        'readWinreg("calendar")' in js_src)
    total += 1; passes += check("readJson('calendar')",
        'readJson("calendar")' in js_src)
    total += 1; passes += check("DEFAULT_CALENDAR_PORT 兜底",
        'return DEFAULT_CALENDAR_PORT' in js_src)
    total += 1; passes += check("module.exports 暴露",
        'readCalendarPort' in js_src.split('module.exports')[1])

    # ─── S8: port_config.js 注释提到 calendar 路径 ───
    print("\n[S8] port_config.js 注释提到 calendar 路径")
    total += 1; passes += check("/prisIragent/calendar 路径(注意大写 I)",
        '/prisIragent/calendar' in js_src)

    # ─── S9: CalendarHandler 类 + 方法 ───
    print("\n[S9] prisIragent_web.py CalendarHandler")
    total += 1; passes += check("类定义",
        'class CalendarHandler(BaseHTTPRequestHandler):' in web_src)
    total += 1; passes += check("do_GET",
        'def do_GET(self):' in web_src.split('class CalendarHandler')[1].split('# =====')[0])
    total += 1; passes += check("do_POST",
        'def do_POST(self):' in web_src.split('class CalendarHandler')[1].split('# =====')[0])
    total += 1; passes += check("do_OPTIONS(CORS)",
        'def do_OPTIONS(self):' in web_src.split('class CalendarHandler')[1].split('# =====')[0])
    total += 1; passes += check("_json 方法",
        'def _json(' in web_src.split('class CalendarHandler')[1])

    # ─── S10: CalendarHandler 拒绝非日历路径 ───
    print("\n[S10] CalendarHandler 拒绝非日历路径")
    ch_section = web_src.split('class CalendarHandler')[1].split('def main():')[0]
    total += 1; passes += check("rejected 日志 + 404",
        'not a calendar endpoint' in ch_section and '404' in ch_section)

    # ─── S11: /__calendar_ready 健康端点 ───
    print("\n[S11] /__calendar_ready 健康端点")
    total += 1; passes += check("路径",
        '/__calendar_ready' in ch_section)
    total += 1; passes += check("返 ok + service=calendar",
        '"service": "calendar"' in ch_section or '\"service\": \"calendar\"' in ch_section)

    # ─── S12: _serve_calendar_static 方法 ───
    print("\n[S12] CalendarHandler._serve_calendar_static")
    total += 1; passes += check("方法定义",
        'def _serve_calendar_static(self, filename:' in ch_section or
        'def _serve_calendar_static(self,' in ch_section)
    total += 1; passes += check("os.path.basename 防穿越",
        'os.path.basename' in ch_section)
    total += 1; passes += check("prisIragent_calendar/static 路径",
        'prisIragent_calendar' in ch_section and 'static' in ch_section)

    # ─── S13: --calendar-port CLI flag ───
    print("\n[S13] main() argparse 含 --calendar-port")
    main_section = web_src.split('\ndef main():\n', 1)[1].split('if __name__ ==')[0]
    total += 1; passes += check("--calendar-port flag",
        '"--calendar-port"' in main_section or "'--calendar-port'" in main_section)
    total += 1; passes += check("resolve_start_port('calendar', ...)",
        '_resolve_cal' in main_section or 'resolve_start_port' in main_section)
    total += 1; passes += check("env: PRISIRAGENT_CALENDAR_PORT",
        'PRISIRAGENT_CALENDAR_PORT' in main_section)

    # ─── S14: _CONFIGURED_CALENDAR_PORT / _REAL_CALENDAR_PORT 全局 ───
    print("\n[S14] 日历端口 configured/actual 全局")
    total += 1; passes += check("_CONFIGURED_CALENDAR_PORT: int 声明",
        '_CONFIGURED_CALENDAR_PORT: int' in web_src)
    total += 1; passes += check("_REAL_CALENDAR_PORT: int 声明",
        '_REAL_CALENDAR_PORT: int' in web_src)

    # ─── S15: /api/port_status calendar 字段 ───
    print("\n[S15] /api/port_status 返 calendar 字段")
    port_status_section = web_src.split('elif path == "/prisiragent/api/port_status":')[1].split('elif path == ')[0]
    total += 1; passes += check("\"calendar\": 块",
        '"calendar":' in port_status_section)
    total += 1; passes += check("calendar.configured",
        'cal_configured' in port_status_section)
    total += 1; passes += check("calendar.actual",
        'cal_actual' in port_status_section)
    total += 1; passes += check("calendar.changed",
        'cal_changed' in port_status_section)
    total += 1; passes += check("calendar.enabled 字段",
        '"enabled":' in port_status_section and 'cal_actual' in port_status_section)

    # ─── S16: 双端口 bootstrap + sentinel ───
    print("\n[S16] 双端口 bootstrap + sentinel")
    boot_section = web_src.split('srv = ThreadingHTTPServer((WEB_HOST, args.port)')[1]
    total += 1; passes += check("CalendarHandler 启动 ThreadingHTTPServer",
        'ThreadingHTTPServer' in boot_section and 'CalendarHandler' in boot_section)
    total += 1; passes += check("PRISIR_CALENDAR_READY sentinel",
        'PRISIR_CALENDAR_READY' in boot_section)
    total += 1; passes += check("后台线程跑日历端口",
        '_cal_thread' in boot_section and 'daemon=True' in boot_section)
    total += 1; passes += check("--calendar-port=0 禁用分支",
        'disabled' in boot_section)

    # ─── S17: 日历端口失败兜底 ───
    print("\n[S17] 日历端口 bind 失败兜底")
    total += 1; passes += check("OSError 捕获",
        'OSError' in boot_section and 'calendar routes stay on main web port' in boot_section)

    # ─── S18: Rust port_config.rs DEFAULT_CALENDAR_PORT = 18803 ───
    print("\n[S18] port_config.rs DEFAULT_CALENDAR_PORT = 18803")
    total += 1; passes += check("pub const DEFAULT_CALENDAR_PORT",
        re.search(r'pub\s+const\s+DEFAULT_CALENDAR_PORT:\s*u16\s*=\s*18803', rs_src) is not None)
    total += 1; passes += check("P2.5+14 注释",
        'P2.5+14' in rs_src)

    # ─── S19: Rust read_calendar_port 函数 ───
    print("\n[S19] port_config.rs read_calendar_port")
    total += 1; passes += check("pub fn read_calendar_port",
        re.search(r'pub\s+fn\s+read_calendar_port\(\)', rs_src) is not None)
    total += 1; passes += check("调 read_port('calendar', ...)",
        'read_port("calendar", DEFAULT_CALENDAR_PORT)' in rs_src)

    # ─── S20: Rust 测试覆盖 ───
    print("\n[S20] Rust 默认值测试")
    test_section = rs_src.split('mod tests')[1] if 'mod tests' in rs_src else ''
    total += 1; passes += check("assert_eq DEFAULT_CALENDAR_PORT = 18803",
        'DEFAULT_CALENDAR_PORT, 18803' in test_section)

    # ─── S21: py_compile prisIragent_web.py OK ───
    print("\n[S21] py_compile prisIragent_web.py")
    try:
        r = subprocess.run(['python', '-c', 'import py_compile; py_compile.compile("prisIragent_web.py", doraise=True)'],
                          capture_output=True, text=True, timeout=20, cwd=ROOT)
        total += 1; passes += check(f"py_compile ({r.returncode})",
            r.returncode == 0, detail=r.stderr[:150] if r.returncode != 0 else '')
    except Exception as e:
        total += 1; passes += check(f"py_compile fail: {e}", False)

    # ─── S22: port_config.py 烟雾测试 ───
    print("\n[S22] port_config.py 烟雾测试")
    try:
        r = subprocess.run(['python', '-c',
            "from companion.music.port_config import DEFAULT_CALENDAR_PORT, read_port, resolve_start_port, notify_port_changed, pick_free_port; "
            "print('DEFAULT_CALENDAR_PORT:', DEFAULT_CALENDAR_PORT); "
            "print('read_calendar:', read_port('calendar', DEFAULT_CALENDAR_PORT)); "
            "print('resolve:', resolve_start_port('calendar', None, None, DEFAULT_CALENDAR_PORT)); "
            "print('pick_free:', pick_free_port(DEFAULT_CALENDAR_PORT))"],
            capture_output=True, text=True, timeout=10, cwd=ROOT)
        out = r.stdout + r.stderr
        ok_18803 = 'DEFAULT_CALENDAR_PORT: 18803' in out
        total += 1; passes += check("DEFAULT_CALENDAR_PORT = 18803", ok_18803,
            detail=out[:200] if not ok_18803 else '')
    except Exception as e:
        total += 1; passes += check(f"smoke fail: {e}", False)

    print("\n" + "=" * 60)
    print(f"Result: {passes}/{total} checks passed")
    print("=" * 60)
    return 0 if passes == total else 1

if __name__ == '__main__':
    sys.exit(main())
