# -*- mode: python ; coding: utf-8 -*-

# 2026-08-24 修:litellm 运行时读 package 自带 JSON(model_prices_and_context_window_backup.json
#   等 model 价格/上下文表),PyInstaller 默认不打 package data → 对话崩
#   FileNotFoundError: litellm/model_prices_and_context_window_backup.json。
#   用 collect_data_files 收全 litellm 的 json/yaml 等非 .py 资源。
from PyInstaller.utils.hooks import collect_data_files

litellm_datas = collect_data_files('litellm')

a = Analysis(
    ['prisiragent_web.py'],
    pathex=[],
    binaries=[],
    datas=litellm_datas + [
        ('assets', 'assets'),
        # 2026-08-24 v2.3.0:方案库索引 + 项目宪法打进 _MEIPASS/docs/。
        #   _shell_system_prompt 从 _REPO_ROOT/docs/(frozen 下=_MEIPASS/docs/)读
        #   preset-solutions-index.md(预设优先级)和 prisir-dev-constitution.md(宪法);
        #   不打进包 frozen 下两文件缺失 → 方案库索引/宪法静默失效。
        ('docs/preset-solutions-index.md', 'docs'),
        ('docs/prisir-dev-constitution.md', 'docs'),
        # 2026-08-24 v2.3.0:用户画像沉淀模块。prisiragent_web 用「函数内 import user_profile」
        #   (lazy import,_shell_system_prompt / _run_chat_thread 两处),PyInstaller 静态
        #   分析抓不到函数内 import → 不打进包则 frozen 下画像能力静默失效。模块纯 stdlib,
        #   datas 拷到 _MEIPASS 根(sys.path 含 _MEIPASS),`import user_profile` 直接命中。
        ('user_profile.py', '.'),
        # 2026-08-24:方案库自学习闭环模块。prisiragent_web 函数内 `import solutions_learner`
        #   (lazy import),PyInstaller 抓不到 → 显式打进,否则 frozen 下学习/主题聚类静默失效。
        ('solutions_learner.py', '.'),
        # 2026-08-24:宪法合规检测器(改后检测复用)。prisiragent_web `_review_written_files` 函数内
        #   `import constitution_compliance`(lazy import)扫 write_file 写的代码文件。PyInstaller
        #   抓不到函数内 import → 显式打进 _MEIPASS 根,否则 frozen 下改后检测静默失效。
        #   只读复用内核,不修改本体。
        ('constitution_compliance.py', '.'),
        # 2026-08-25:探囊(本机搜索)配进成品。prisiragent_web `_findex()`/`_fcontent()` 函数内
        #   lazy import shell_findex / prisir_fcontent,PyInstaller 抓不到 → 显式 datas 拷入。
        #   findex = Rust cdylib(prisir_findex.dll,2MB)+ Python 壳(shell_findex/rebuild/reputation);
        #     只拷 .py + dll,不带 target/ 编译产物、findex.db 本机库、__pycache__。
        #     _dll 路径在 frozen 下解析为 _MEIPASS/prisir_findex/target/release/(只读但可读,OK)。
        ('prisir_findex/shell_findex.py', 'prisir_findex'),
        ('prisir_findex/rebuild.py', 'prisir_findex'),
        ('prisir_findex/reputation.py', 'prisir_findex'),
        ('prisir_findex/target/release/prisir_findex.dll', 'prisir_findex/target/release'),
        #   fcontent = 纯 Python 包(FTS5 走 sqlite 内置,零三方依赖跑 .md/.txt/代码);
        #     docx/pdf/pptx 库与 OCR(rapidocr)不打包 → 检索时诚实降级提示,不影响 md/txt 核心。
        #     只拷 .py,不带 fcontent.db/models/screenshots/__pycache__。
        ('prisir_fcontent/__init__.py', 'prisir_fcontent'),
        ('prisir_fcontent/engine.py', 'prisir_fcontent'),
        ('prisir_fcontent/extract.py', 'prisir_fcontent'),
        ('prisir_fcontent/tokenize.py', 'prisir_fcontent'),
        ('prisir_fcontent/overlay_translate.py', 'prisir_fcontent'),
        ('prisir_fcontent/ocr_eval.py', 'prisir_fcontent'),
        ('prisir_fcontent/verify.py', 'prisir_fcontent'),
        # 2026-08-24 修:tiktoken cl100k_base BPE 数据离线随包(tiktoken_data/cl100k_base.tiktoken,
        #   取自本机 data-gym-cache,sha1(url) 文件名)。litellm token 计数 frozen 下联网下载
        #   不可靠,预置进 _MEIPASS/tiktoken_cache/data-gym-cache/ 走离线缓存。
        #   cache_key = sha1("https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken")
        #   = 9b5ad71b2ce5302211f9c61530b329a4922fc6a4
        ('tiktoken_data/cl100k_base.tiktoken', 'tiktoken_cache/data-gym-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4'),
        # v1.0 权限闸:子包随 exe 一起打进 _MEIPASS,确保 import prisiragent_coworker.permissions
        # 在 onefile 解包后能找到。同时把 perm_gate.py 显式带上(PyInstaller 自动分析
        # 已能跟入,但显式列出来万一后续改成 lazy import 不会断)。
        ('prisiragent_coworker', 'prisiragent_coworker'),
        ('perm_gate.py', '.'),
        # 2026-08-25 P1:局域网联动配对/令牌/mDNS 模块。prisiragent_web 顶层 `import lan_pair`,
        # PyInstaller 静态分析能跟入,但显式 datas 双保险(防后续改 lazy import 断链)。纯 stdlib。
        ('lan_pair.py', '.'),
        # 上游法律文件随子包一起被打入 _MEIPASS(prisiragent_coworker 子目录内已有)
    ],
    hiddenimports=['user_profile', 'solutions_learner', 'constitution_compliance', 'lan_pair', 'shell_findex', 'prisir_fcontent', 'prisir_fcontent.engine', 'prisir_fcontent.extract', 'prisir_fcontent.tokenize', 'prisir_fcontent.overlay_translate', 'prisir_fcontent.ocr_eval', 'fastlane.providers.llm_prisir', 'fastlane.providers.base', 'fastlane.providers.factory', 'fastlane.providers.llm_cloud', 'fastlane.adapters', 'fastlane.adapters.main', 'litellm', 'httpx', 'httpcore', 'h11', 'anyio', 'sniffio', 'certifi', 'idna', 'openai', 'anthropic', 'tiktoken', 'jsonschema', 'jinja2', 'aiohttp'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 2026-08-24 修:pydantic 从 excludes 移除——litellm/openai/anthropic 客户端顶层
    #   硬 import pydantic(openai._models / litellm.types.utils),对话路径 smart·custom
    #   一配端点就崩 ModuleNotFoundError: No module named 'pydantic'。fastapi/uvicorn/
    #   starlette 保留排除(对话链不真起 fastapi 服务器,adapters.main 的 fastapi import
    #   仅在跑 ASR/LLM 本地服务时才需要,当前对话不走那条)。
    excludes=['torch', 'torchaudio', 'torchvision', 'tensorflow', 'transformers', 'vllm', 'accelerate', 'modelscope', 'numba', 'pandas', 'scipy', 'matplotlib', 'onnxruntime', 'rapidocr_onnxruntime', 'cv2', 'fastapi', 'uvicorn', 'starlette', 'langchain', 'langchain_core', 'langsmith', 'IPython', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PrisirAI',
    icon='prisiragent-shell/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
