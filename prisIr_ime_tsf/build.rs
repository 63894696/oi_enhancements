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
        if let Err(e) = res.compile() {
            // 编译失败不 panic — 打印 warning,让 DLL 仍能 build(只是没图标)。
            // 真机部署时若缺图标,AddItem 仍会 E_FAIL,日志能看出来。
            println!("cargo:warning=winres compile failed (icon not embedded): {e}");
        }
    }
    // ico 变化时触发重编。
    println!("cargo:rerun-if-changed=pinyin.ico");
}
