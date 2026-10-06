---
name: ntfs-case-folding-double-path-cleanup
description: NTFS case-folding 导致 git 同时跟踪 prisiragent_web.py + prisIragent_web.py 两个 case-folding 的 path;2910718 commit 改了小写 i 但 HEAD 没收到。2026-10-06 commit da07efe 用 git update-index --add --cacheinfo + git rm --cached 合并到一个 path。
metadata:
  type: project
---

# NTFS case-folding 双 path cleanup(2026-10-06 commit da07efe)

## 背景

P3.0 music 归档期间 commit `2910718 fix(web): port_config 从 companion/music/ 救回主仓根 + 测试清理 (P3.0)` 声称改了 `prisiragent_web.py`(小写 i) `from companion.music.port_config` → `from port_config`(4 处)。commit message 描述很详细,smoke 还通过了。

但实际上 **HEAD 上的 `prisIragent_web.py`(大写 I,git index 跟踪的)blob 没变**!

## 根因 — NTFS case-folding + git 双 path

`git ls-files | grep agent_web` 返两个条目:
```
prisIragent_web.py
prisiragent_web.py
```

HEAD 上两个 path 有**不同的 blob**:
- `prisIragent_web.py`(大写 I)= `5596b6412...`(老 import,4 处 `companion.music.port_config`)
- `prisiragent_web.py`(小写 i)= `d6f480944...`(新 import,4 处 `from port_config`)

但 NTFS 是 case-insensitive + case-preserving:`os.stat` 两个 path 拿到同一 inode(10133099162363589)。disk 上实质只有一个文件。

2910718 commit 时:
- 可能在 Windows 上 `git add prisIragent_web.py` 时 NTFS 把大小写折叠,小写 i 路径被 git index 记为「修改」
- 大写 I 路径(HEAD 真正跟踪的)blob 没动
- commit message 描述的「修了 4 处 import」只在 disk 上实际生效(因为磁盘是同一个),但 git 历史里看,HEAD 的 prisIragent_web.py 没动

session-resumption 后我编辑 disk 文件(`prisIragent_web.py` 看似 64 行 diff),但 diff 是相对于 `prisIragent_web.py`(小写 i)那条 path 的 stage。`prisIragent_web.py`(大写 I,HEAD 跟踪的) blob 仍是老 5596b64。

## 修复 — update-index + cacheinfo + git rm --cached

需要把两个 path entry 合并到一个:

1. **把大写 I 路径指向新 blob**:
   ```bash
   git update-index --add --cacheinfo 100644,d6f480944c7afa901aa9c63e4acd69b6eb744461,prisIragent_web.py
   ```
   这一步让大写 I 那条 path 的 stage entry 从 `5596b64` 变成 `d6f4809`(指向新内容)。

2. **删小写 i path entry**:
   ```bash
   git rm --cached prisiragent_web.py
   ```
   把小写 i path 从 index 移除。disk 上文件不动(`--cached`)— 但因 NTFS case-folding,实际 file 改了 case 保留名字。`ls -i` 验证只剩一个 inode。

3. **commit**:
   ```
   M prisIragent_web.py    (blob 改)
   D prisiragent_web.py   (path entry 删)
   ```

commit `da07efe fix(web+ext-bridge): P2.5+21/22 hotfix ship + NTFS case-folding 修复`。

## 验证

- `git ls-files | grep agent_web` 只剩 `prisIragent_web.py` 一个
- HEAD:blob `d6f4809`
- `grep -c "companion.music.port_config" prisIragent_web.py` 返 0(已修)
- 5+16+18 测试绿

## 教训

NTFS 上 git 跟大小写折叠的坑不止「git add 静默失败」(`ntfs-case-folding-git-add-fail.md`),还有「git 同时跟踪两个 case-folding path 但磁盘是同一文件」这种更深的状态混乱。

**为什么之前没发现**:每次跑 pytest 都过 — 因为 NTFS 文件系统层面 import `prisIragent_web` 能找到磁盘上的文件(Python 的 import 也走 OS,接受 NTFS case folding),所以 `from port_config import` 在 disk 上生效,代码运行没问题。

**怎么预防**:
- 任何重命名 commit 必跑 `git ls-files | grep -i <name>` 看有没有 case-folding 双 path
- commit 前 `git diff --stat HEAD` 看修改行数;若 diff 显示「M prisIragent_web.py + 78 行」但 disk 上改动远超 78 行 → 警惕大小写双 path
- 写 memory 时把 NTFS case-folding 视为「先 commit → 再用 update-index 合并 path entry」的标准两步,不要寄希望于「commit 一下就好了」

**Why:** git 在 case-folding 文件系统上能「同时跟踪」两个 case 的 path,这是文件系统决定(磁盘是同一文件),不是 git 主动行为。commit 改了 path entry 不一定改了真正被 import 的文件 — 因为 Python `import prisIragent_web` 在 NTFS 上接受任何 case,跟 disk 实际 case 无关。

**How to apply:** 以后 commit `prisIragent_web.py` 任何改动前必跑 `git ls-files | grep -i agent_web` 确认只有一条 path。如果两条,先 `git update-index --add --cacheinfo <新blob>,<大写path>` + `git rm --cached <小写path>` 合并再 commit。