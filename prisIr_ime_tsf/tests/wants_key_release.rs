//! #104 五轮:wants_key_state_full 标点/翻页契约 —— 跑在 **release 编译的库** 上
//! (integration test 链接 release rlib),铁证 release 二进制行为与源码一致,
//! 防 rustc 1.95 对「guard arm + 多重 OR 模式 + 宽臂」的错编把 Shift+- 错放行出 _。
//!
//! 背景:VM ActivateEx 指纹曾显示 wants_key_state_full(0xBD,...,chinese_mode=true)
//! 在 release 下返 false(应为 true),0xBD 错走翻页臂 has_prev=false,到不了 OEM 标点臂。

use prisir_ime_tsf::tsf_input_processor::TsfInputProcessor as T;

#[test]
fn wants_key_0xbd_contract_release() {
    // 中文模式,空 buffer/无候选:
    // shift+0xBD → 吃(出 ——);shift+0xBB → 吃(出 ￥)
    assert!(T::wants_key_state_full(0xBD, true, true, false, false, true, true), "shift+0xBD 中文模式必须吃");
    assert!(T::wants_key_state_full(0xBB, true, true, false, false, true, true), "shift+0xBB 中文模式必须吃");
    // 未 shift 0xBD/0xBB 在中文模式一律吃(无目标页时 OnKeyDown commit 符号,有则翻页)
    assert!(T::wants_key_state_full(0xBD, true, true, false, false, true, false));
    assert!(T::wants_key_state_full(0xBB, true, true, false, false, true, false));
    // 未 shift 且有上一页 → 吃(翻页)
    assert!(T::wants_key_state_full(0xBD, false, false, false, true, true, false));
    // 英文模式:0xBD/0xBB 一律放行(系统出 -/_ / =/+)
    assert!(!T::wants_key_state_full(0xBD, true, true, false, false, false, true));
    assert!(!T::wants_key_state_full(0xBD, true, true, false, false, false, false));
    assert!(!T::wants_key_state_full(0xBB, true, true, false, false, false, false));
}

#[test]
fn map_punct_0xbd_release() {
    assert_eq!(T::map_punct(0xBD, true, true), Some("\u{2014}\u{2014}")); // shift+- 中文标点 → ——
    assert_eq!(T::map_punct(0xBD, false, true), Some("-"));
}
