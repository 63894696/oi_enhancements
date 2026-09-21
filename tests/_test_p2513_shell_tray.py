"""P2.5+13 Electron 壳托盘加语伴/音乐/日历 (2026-09-22)

覆盖:
  S1: port_config.js DEFAULT_CALENDAR_PORT 常量定义
  S2: port_config.js readCalendarPort 函数 + 导出
  S3: port_config.js 模块默认与 Python 端对齐(18802/18850/0)
  S4: main.js openInShell(url, label) helper 函数
  S5: main.js openCompanionWindow() + 读 readCompanionPort + WEB_HOST
  S6: main.js openMusicWindow() + 读 readMusicPort + 0 端口兜底
  S7: main.js openCalendarWindow() + 读 readCalendarPort + /prisiragent/calendar 路径
  S8: main.js createTray() trayItems 数组加 3 菜单项(label + click)
  S9: main.js 3 菜单项在「开机自启」之后、「开发者模式」/「退出」之前的位置
  S10: node --check port_config.js + main.js 双文件语法 OK
"""
import os, re, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT_CFG = os.path.join(ROOT, 'prisiragent-shell', 'port_config.js')
MAIN_JS = os.path.join(ROOT, 'prisiragent-shell', 'main.js')

def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()

def check(name, ok, detail=''):
    mark = '✓' if ok else '✗'
    print(f"  [{mark}] {name}{(': ' + detail) if detail else ''}")
    return 1 if ok else 0

def main():
    cfg_src = read(PORT_CFG)
    main_src = read(MAIN_JS)
    print("=" * 60)
    print("P2.5+13 Electron 壳托盘语伴/音乐/日历 静态扫")
    print("=" * 60)
    passes = 0; total = 0

    # ─── S1: DEFAULT_CALENDAR_PORT 常量 ───
    print("\n[S1] port_config.js DEFAULT_CALENDAR_PORT 常量")
    total += 1; passes += check("DEFAULT_CALENDAR_PORT 定义",
        'const DEFAULT_CALENDAR_PORT' in cfg_src)
    total += 1; passes += check("DEFAULT_CALENDAR_PORT 复用 DEFAULT_WEB_PORT",
        'DEFAULT_CALENDAR_PORT = DEFAULT_WEB_PORT' in cfg_src)
    total += 1; passes += check("注释说明日历走 web /prisiragent/calendar",
        '/prisiragent/calendar' in cfg_src)

    # ─── S2: readCalendarPort 函数 ───
    print("\n[S2] port_config.js readCalendarPort 函数")
    rc_section = cfg_src.split('const readCalendarPort')[1][:600] if 'const readCalendarPort' in cfg_src else ''
    total += 1; passes += check("函数定义",
        'const readCalendarPort =' in cfg_src)
    total += 1; passes += check("HKCU 优先 readWinreg('calendar')",
        'readWinreg("calendar")' in rc_section)
    total += 1; passes += check("JSON 兜底 readJson('calendar')",
        'readJson("calendar")' in rc_section)
    total += 1; passes += check("默认 DEFAULT_CALENDAR_PORT 兜底",
        'return DEFAULT_CALENDAR_PORT' in rc_section)
    total += 1; passes += check("module.exports 暴露 readCalendarPort",
        'readCalendarPort' in cfg_src.split('module.exports')[1])
    total += 1; passes += check("module.exports 暴露 DEFAULT_CALENDAR_PORT",
        'DEFAULT_CALENDAR_PORT' in cfg_src.split('module.exports')[1])

    # ─── S3: 模块默认与 Python 端对齐 ───
    print("\n[S3] 模块默认与 Python 端对齐")
    total += 1; passes += check("DEFAULT_WEB_PORT = 18802",
        'const DEFAULT_WEB_PORT = 18802' in cfg_src)
    total += 1; passes += check("DEFAULT_COMPANION_PORT = 18850",
        'const DEFAULT_COMPANION_PORT = 18850' in cfg_src)
    total += 1; passes += check("DEFAULT_MUSIC_PORT = 0",
        'const DEFAULT_MUSIC_PORT = 0' in cfg_src)

    # ─── S4: openInShell helper ───
    print("\n[S4] main.js openInShell(url, label) helper")
    total += 1; passes += check("函数定义",
        'function openInShell(url, label)' in main_src)
    total += 1; passes += check("win 不存在则 createWindow",
        'if (!win) createWindow()' in main_src)
    total += 1; passes += check("win.show() + win.focus()",
        'win.show();' in main_src and 'win.focus();' in main_src)
    total += 1; passes += check("win.loadURL(url) 加载目标页",
        'win.loadURL(url)' in main_src)
    total += 1; passes += check("logInfo trayOpen 标签",
        'logInfo("trayOpen"' in main_src)

    # ─── S5: openCompanionWindow ───
    print("\n[S5] main.js openCompanionWindow()")
    total += 1; passes += check("函数定义",
        'function openCompanionWindow()' in main_src)
    total += 1; passes += check("调 port_config.readCompanionPort()",
        'require("./port_config").readCompanionPort()' in main_src)
    total += 1; passes += check("用 WEB_HOST 而非 localhost(与其他代码一致)",
        'WEB_HOST' in main_src.split('function openCompanionWindow')[1][:500])
    total += 1; passes += check("通过 openInShell 加载",
        'openInShell(`http://${WEB_HOST}:${port}/`' in main_src or
        'openInShell(`http://${WEB_HOST}:' in main_src)

    # ─── S6: openMusicWindow ───
    print("\n[S6] main.js openMusicWindow()")
    total += 1; passes += check("函数定义",
        'function openMusicWindow()' in main_src)
    total += 1; passes += check("调 port_config.readMusicPort()",
        'require("./port_config").readMusicPort()' in main_src)
    total += 1; passes += check("0 端口兜底回主面板",
        'port <= 0' in main_src.split('function openMusicWindow')[1][:800] or
        'port < 1' in main_src.split('function openMusicWindow')[1][:800])
    total += 1; passes += check("music 标签 'music'",
        '"music"' in main_src.split('function openMusicWindow')[1][:800])

    # ─── S7: openCalendarWindow ───
    print("\n[S7] main.js openCalendarWindow()")
    total += 1; passes += check("函数定义",
        'function openCalendarWindow()' in main_src)
    total += 1; passes += check("调 port_config.readCalendarPort()",
        'require("./port_config").readCalendarPort()' in main_src)
    total += 1; passes += check("加载 /prisiragent/calendar 路由",
        '/prisiragent/calendar' in main_src.split('function openCalendarWindow')[1][:800])

    # ─── S8: createTray() trayItems 加 3 菜单项 ───
    print("\n[S8] createTray() trayItems 加 3 菜单项")
    tray_section = main_src.split('function createTray')[1][:3000]
    total += 1; passes += check("「语伴」菜单项 label",
        '"语伴"' in tray_section and 'openCompanionWindow' in tray_section)
    total += 1; passes += check("「音乐」菜单项 label",
        '"音乐"' in tray_section and 'openMusicWindow' in tray_section)
    total += 1; passes += check("「📅 打开日历」菜单项 label",
        '"📅 打开日历"' in tray_section and 'openCalendarWindow' in tray_section)
    total += 1; passes += check("3 菜单项前有 separator",
        '{ type: "separator" }' in tray_section.split('"语伴"')[0][-200:])

    # ─── S9: 菜单项位置(开机自启之后,开发者模式/退出之前) ───
    print("\n[S9] 菜单项位置")
    # 用精确 marker:trayItems.push({ label: "..." 是动态 push,静态数组用 "..."
    autostart_pos = tray_section.find('"开机自启"')
    companion_pos = tray_section.find('"语伴"')
    music_pos = tray_section.find('"音乐"')
    calendar_pos = tray_section.find('"📅 打开日历"')
    dev_push_pos = tray_section.find('trayItems.push({ label: "开发者模式"')
    quit_pos = tray_section.find('trayItems.push({ label: "退出"')
    total += 1; passes += check("语伴 在 开机自启 之后",
        0 <= autostart_pos < companion_pos)
    total += 1; passes += check("音乐 在 语伴 之后",
        0 <= companion_pos < music_pos)
    total += 1; passes += check("日历 在 音乐 之后",
        0 <= music_pos < calendar_pos)
    total += 1; passes += check("日历 在 退出 之前",
        0 <= calendar_pos < quit_pos)
    # dev 模式是 push 项(可能不存在),用 trayItems.push 锚定;存在则必须在日历之后
    if dev_push_pos > 0:
        total += 1; passes += check("开发者模式 push 在 日历 之后",
            0 <= calendar_pos < dev_push_pos)
    else:
        total += 1; passes += check("开发者模式 不存在(普通用户装包,跳过位置检查)", True)

    # ─── S10: 编译检查 ───
    print("\n[S10] 编译检查")
    try:
        r = subprocess.run(['node', '--check', PORT_CFG], capture_output=True, text=True, timeout=10)
        total += 1; passes += check(f"port_config.js node --check ({r.returncode})",
            r.returncode == 0,
            detail=r.stderr[:120] if r.returncode != 0 else '')
    except Exception as e:
        total += 1; passes += check(f"port_config.js node --check fail: {e}", False)
    try:
        r = subprocess.run(['node', '--check', MAIN_JS], capture_output=True, text=True, timeout=10)
        total += 1; passes += check(f"main.js node --check ({r.returncode})",
            r.returncode == 0,
            detail=r.stderr[:120] if r.returncode != 0 else '')
    except Exception as e:
        total += 1; passes += check(f"main.js node --check fail: {e}", False)

    print("\n" + "=" * 60)
    print(f"Result: {passes}/{total} checks passed")
    print("=" * 60)
    return 0 if passes == total else 1

if __name__ == '__main__':
    sys.exit(main())
