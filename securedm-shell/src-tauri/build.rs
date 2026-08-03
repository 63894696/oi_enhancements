fn main() {
    // 关键:必须在 AppManifest 里声明自定义命令,否则 has_app_acl=false,
    // allowed_commands.json 不含 pick_file/pick_folder,generate_handler! 宏
    // 会在编译期把它们当 unused 命令移除 → 运行时报 "Plugin not found"。
    // AppManifest 没有 default_permission 方法;改为 commands() 保留命令注册,
    // 再在 src-tauri/permissions/pick.toml 手写 app:default 放开这两条命令。
    let attrs = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["pick_file", "pick_folder"]),
    );
    tauri_build::try_build(attrs).expect("tauri build 失败");
}
