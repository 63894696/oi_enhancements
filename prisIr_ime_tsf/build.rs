// T3/#88 LangBar: 把 pinyin.ico 编译进 DLL 的图标资源段。
//
// 为什么需要(2026-09-01 诊断):
//   ITfLangBarItemMgr::AddItem 据我们 item 的 GetInfo.clsidService 反查 TIP profile,
//   读 profile 的 IconFile(=C:\PrisirIME\prisir_ime_tsf.dll) + IconIndex(=0) 调
//   ExtractIcon 提取图标。我们 DLL 之前没编任何图标资源 → ExtractIcon 拿不到 →
//   AddItem 直接 E_FAIL 0x80004005(日志证实 mgr 调了 GetInfo/GetStatus 就失败,
//   永远走不到 GetIcon)。
//   嵌入 pinyin.ico 后,IconIndex 0x0 对应资源里第一个图标,mgr 提取成功。
//
// 资源 ID 用 1 (IDI_ICON1),winres 默认 set_icon 会生成 IDI_ICON 1。
// register.rs 里 IconIndex 写 0x0 → 提取资源段第一个图标,正好命中。
fn main() {
    // 只在 Windows 目标嵌资源(cross 编译到非 Windows 时 winres 无用)。
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winres::WindowsResource::new();
        // pinyin.ico 在仓库根。winres 会把第一个 set_icon 编成 IDI_ICON=1 的主图标。
        res.set_icon("pinyin.ico");

        // 2026-09-03 IMM32:`.ime` 必须带 VFT_DRV(0x3) + VFT2_DRV_INPUTMETHOD(0xB) 版本资源,
        // 否则 IMM32!ImmLoadLayout 拒载(记忆 prisirtip-imm32-research)。winres 有
        // set_version_info(FILETYPE/FILESUBTYPE),直接用。
        res.set_version_info(winres::VersionInfo::FILETYPE, 0x3);   // VFT_DRV
        res.set_version_info(winres::VersionInfo::FILESUBTYPE, 0xB); // VFT2_DRV_INPUTMETHOD
        res.set("FileDescription", "Prisir IME (TSF + IMM32 dual-stack)");
        res.set("ProductName", "Prisir IME");
        res.set_language(0x0804); // 简体中文

        if let Err(e) = res.compile() {
            // 编译失败不 panic — 打印 warning,让 DLL 仍能 build(只是没图标)。
            // 真机部署时若缺图标,AddItem 仍会 E_FAIL,日志能看出来。
            println!("cargo:warning=winres compile failed (icon not embedded): {e}");
        }

        // 2026-09-03 IMM32:把 .def 传给 cdylib 链接器。
        // imm_only feature:用纯 IMM 的 .def(零 COM 导出)→ 产 prisir_ime_imm.ime(对标搜狗 SogouPY.ime)。
        // 默认(TSF):双导出 .def → prisir_ime_tsf.dll(COM) + 旧双栈 .ime。
        // MSVC 链接器吃 /DEF:<path>。用 manifest 目录绝对路径,避免相对路径在增量/不同 cwd 下失效。
        let def_name = if std::env::var("CARGO_FEATURE_IMM_ONLY").is_ok() {
            "prisir_ime_imm.def"
        } else {
            "prisir_ime_tsf.def"
        };
        let def = format!(
            "cargo:rustc-cdylib-link-arg=/DEF:{}\\{}",
            std::env::var("CARGO_MANIFEST_DIR").unwrap(),
            def_name
        );
        println!("{def}");
    }
    // ico 变化时触发重编。
    println!("cargo:rerun-if-changed=pinyin.ico");
    println!("cargo:rerun-if-changed=prisir_ime_tsf.def");
    println!("cargo:rerun-if-changed=prisir_ime_imm.def");
}
