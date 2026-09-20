# 预设方案库索引 (Preset Solutions Index)

> PrisirAI 对话壳「优先查位置」路由表。当用户问题命中下表类别,agent 应优先用
> `read_file`/`read_file_head` 查对应路径的已知方案,没覆盖再全网搜。
>
> 加载机制:`prisiragent_web.py::_load_preset_rows()` 解析每行 `| **<类别>** | ... |`。
> 失败静默,文件不存在即视为空(不影响对话,只是不注入)。

| **类别** | **优先查路径** | **备注** |
| --- | --- | --- |
| **输入法问题** | `prisIr_ime/`、`shell_debug.log` | 灵犀 TSF/拼音/五笔/语音 |
| **装包问题** | `installer/prisirai.nsi`、`installer/_staging/` | NSIS + PyInstaller frozen |
| **VM 通道问题** | `docs/vm001-reverse-ps-channel.md`、`prisiragent_web.py` | 反向 PowerShell 通道 |
| **对话链问题** | `prisIragent_cli.py`、`prisIragent_web.py` | litellm/tiktoken/pydantic |
| **浏览器问题** | `prisIr-browser/`、`custom-hover-translate/` | Chromium MV3 CDP |
| **文件搜索问题** | `prisIr_findex/`、`prisIr_fcontent/`、`AnyTXT` 127.0.0.1:9920 | 全盘+内容+FTS5 |
| **协作问题** | `prisIragent_dev_consumer/`、`docs/oiagent-team-workflow-v2.md` | tasks-code 双闸 |
| **权限问题** | `prisIragent_web.py` 权限闸、`docs/prisIrAI-perm-gate-v1.md` | run_shell/write_file/delete_file 弹卡 |
| **密信问题** | `prisirwork/` SMP、`docs/prisir-android-win-link-design.md` | SimpleX |
| **翻译问题** | `custom-hover-translate/`、`prisIr-screenshot-search.md` | 悬停/漫画/字幕 |
| **视频笔记问题** | `prisIragent_web.py` 视频笔记路径 | B站/YouTube 字幕提取 |
| **VPN 问题** | `prisIr-work/`、`docs/forum-prod-deployment.md` | 代理池/防检测 |
| **论坛问题** | `docs/forum-prod-deployment.md` | bbs.babelspan.com/forum |
| **网站问题** | `docs/prisir-brand-pairuisi.md` | 通天尺规/babelspan/主站 |
| **微信公众号问题** | `docs/wechat-article-fetch-ua-bypass.md` | mp.weixin.qq.com |
| **内容柜问题** | `prisIragent_web.py` 内容柜路径 | a11y/内容提取 |
| **书签分类问题** | `prisIr-browser/` 书签模块 | 收藏夹 |
| **移动端问题** | `docs/prisir-android-win-link-design.md` | Capacitor/MuMu |
| **出行/通勤** | `docs/prisir-android-win-link-design.md`(异地联动)、`prisIragent_web.py` 时间工具 | 会议交通缓冲/通勤时间/异地差旅/接送机 |
