fn main() {
    // tauri-build validates every `bundle.resources` path on every build, dev included, but
    // `binaries/sidecar` is only staged by the release scripts and `/binaries` is gitignored —
    // so a fresh checkout died on `resource path 'binaries/sidecar' doesn't exist`. Dev needs no
    // packaged server (`server_bin()` falls back to the venv) and empty resource dirs are
    // skipped, so a placeholder is enough.
    std::fs::create_dir_all("binaries/sidecar").expect("create the sidecar resource dir");

    // Declare the app's own commands so tauri-build autogenerates the ACL permission files
    // (`allow-<cmd>` / `deny-<cmd>` under the `__app__` manifest). Without this, every
    // `#[tauri::command]` in the invoke_handler is rejected at runtime with
    // "Command <name> not allowed by ACL" when the page is served from a remote origin
    // (http://127.0.0.1:<port> served by the sidecar; local-only capabilities never match it).
    let attributes = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "pick_folder",
            "save_diagnostics",
            "get_autostart",
            "set_autostart",
            "get_keep_awake",
            "set_keep_awake",
            "start_window_drag",
            "openshell_version",
            "docker_host_networking",
            "get_dictation_status",
            "start_dictation",
            "stop_dictation",
            "cancel_dictation",
            "download_dictation_model",
            "cancel_dictation_model_download",
            "verify_dictation_model",
            "mark_dictation_test_passed",
            "delete_dictation_model",
            "dictation_level",
            "check_for_update",
            "download_update",
            "clear_pending_update",
            "install_update",
        ]),
    );

    tauri_build::try_build(attributes).expect("failed to run tauri-build");
}
