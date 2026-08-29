#!/usr/bin/env python3
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "thermalnodim_apply_in_session" in text:
        print(f"[NO-DIM] header already patched: {path}")
        return
    old = "bool powercuff_apply(const char *level);\n"
    new = (
        "bool powercuff_apply(const char *level);\n"
        "// Disables only the three OSThermal backlight mitigation thresholds in\n"
        "// the current SpringBoard RemoteCall session. CPU/GPU throttling and\n"
        "// emergency app-terminate/device-restart thermal behaviors are untouched.\n"
        "bool thermalnodim_apply_in_session(void);\n"
    )
    path.write_text(replace_once(text, old, new, "powercuff.h declaration"), encoding="utf-8")
    print(f"[NO-DIM] patched {path}")


def patch_impl(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "bool thermalnodim_apply_in_session(void)" in text:
        print(f"[NO-DIM] implementation already patched: {path}")
        return

    old_imports = (
        '#import <stdio.h>\n'
        '#import <string.h>\n'
        '#import <unistd.h>\n'
    )
    new_imports = (
        '#import <stdio.h>\n'
        '#import <string.h>\n'
        '#import <unistd.h>\n'
        '#import <dlfcn.h>\n'
        '#import <limits.h>\n'
    )
    text = replace_once(text, old_imports, new_imports, "powercuff.m imports")

    anchor = "static bool valid_level(const char *level) {\n"
    helper = r'''// OSThermalNotification behavior IDs from Apple's public libkern header.
// We intentionally touch only the three backlight behaviors.  Torch limits,
// CPU/GPU power-budget mitigation, app termination and device restart remain
// under the system's normal thermal control.
enum {
    kCyanideThermal70PercentBacklight = 2,
    kCyanideThermal50PercentBacklight = 4,
    kCyanideThermal25PercentBacklight = 6,
    kCyanideThermalAppTerminate       = 8,
    kCyanideThermalDeviceRestart      = 9,
};

static int thermalnodim_level_for_behavior(int behavior)
{
    uint64_t raw = r_dlsym_call(R_TIMEOUT,
                                "_OSThermalNotificationLevelForBehavior",
                                (uint64_t)(uint32_t)behavior,
                                0, 0, 0, 0, 0, 0, 0);
    return (int)(int32_t)raw;
}

static bool thermalnodim_set_behavior_level(int behavior, int level)
{
    r_dlsym_call(R_TIMEOUT,
                 "_OSThermalNotificationSetLevelForBehavior",
                 (uint64_t)(uint32_t)behavior,
                 (uint64_t)(int64_t)level,
                 0, 0, 0, 0, 0, 0);

    int readback = thermalnodim_level_for_behavior(behavior);
    printf("[NO-DIM] behavior=%d target=%d readback=%d\n",
           behavior, level, readback);
    return readback == level;
}

bool thermalnodim_apply_in_session(void)
{
    // Check in Cyanide's own process first. RemoteCall resolves the same
    // shared-cache symbols for SpringBoard, so a missing symbol can fail cleanly
    // without poisoning the active SpringBoard RemoteCall session.
    if (!dlsym(RTLD_DEFAULT, "_OSThermalNotificationLevelForBehavior") ||
        !dlsym(RTLD_DEFAULT, "_OSThermalNotificationSetLevelForBehavior")) {
        printf("[NO-DIM] OSThermal behavior symbols unavailable on this build\n");
        return false;
    }

    int restartLevel = thermalnodim_level_for_behavior(kCyanideThermalDeviceRestart);
    int terminateLevel = thermalnodim_level_for_behavior(kCyanideThermalAppTerminate);

    // Put display dimming at the device-restart threshold. In normal use that
    // makes 70/50/25%% thermal backlight caps unreachable, while keeping the
    // system's emergency shutdown/restart path at its original level.
    int disabledLevel = restartLevel;
    if (disabledLevel <= 0) disabledLevel = terminateLevel;
    if (disabledLevel <= 0) disabledLevel = INT_MAX / 2;

    printf("[NO-DIM] remapping backlight thermal thresholds to %d (terminate=%d restart=%d)\n",
           disabledLevel, terminateLevel, restartLevel);

    uint32_t oldSettle = r_settle_us(0);
    bool ok70 = thermalnodim_set_behavior_level(kCyanideThermal70PercentBacklight,
                                                disabledLevel);
    bool ok50 = thermalnodim_set_behavior_level(kCyanideThermal50PercentBacklight,
                                                disabledLevel);
    bool ok25 = thermalnodim_set_behavior_level(kCyanideThermal25PercentBacklight,
                                                disabledLevel);
    r_settle_us(oldSettle);

    bool ok = ok70 && ok50 && ok25;
    printf("[NO-DIM] backlight thermal mitigation %s\n", ok ? "disabled" : "not fully disabled");
    return ok;
}

'''
    text = replace_once(text, anchor, helper + anchor, "powercuff.m helper insertion")
    path.write_text(text, encoding="utf-8")
    print(f"[NO-DIM] patched {path}")


def patch_settings(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    # Re-assert whenever a SpringBoard RemoteCall session is opened or reused.
    if "[NO-DIM] SpringBoard thermal backlight cap bypass" not in text:
        old = '''static BOOL settings_ensure_springboard_remote_call_locked(void)\n{\n    if (g_springboard_rc_ready) {\n        printf("[SETTINGS] reusing SpringBoard RemoteCall session\\n");\n        return YES;\n    }\n\n    if (init_remote_call_with_first_exception_timeout("SpringBoard",\n                                                      false,\n                                                      kSettingsSpringBoardRCFirstExceptionTimeoutMS) != 0) {\n        printf("[SETTINGS] init_remote_call(SpringBoard) failed\\n");\n        return NO;\n    }\n\n    g_springboard_rc_ready = 1;\n    g_springboard_sandbox_escaped = 0;\n    settings_notify_remote_call_state_changed();\n    return YES;\n}\n'''
        new = '''static BOOL settings_ensure_springboard_remote_call_locked(void)\n{\n    if (g_springboard_rc_ready) {\n        printf("[SETTINGS] reusing SpringBoard RemoteCall session\\n");\n        bool noDimOK = thermalnodim_apply_in_session();\n        printf("[NO-DIM] SpringBoard thermal backlight cap bypass refresh=%d\\n", noDimOK);\n        return YES;\n    }\n\n    if (init_remote_call_with_first_exception_timeout("SpringBoard",\n                                                      false,\n                                                      kSettingsSpringBoardRCFirstExceptionTimeoutMS) != 0) {\n        printf("[SETTINGS] init_remote_call(SpringBoard) failed\\n");\n        return NO;\n    }\n\n    g_springboard_rc_ready = 1;\n    g_springboard_sandbox_escaped = 0;\n    settings_notify_remote_call_state_changed();\n    bool noDimOK = thermalnodim_apply_in_session();\n    printf("[NO-DIM] SpringBoard thermal backlight cap bypass apply=%d\\n", noDimOK);\n    log_user("%s Thermal screen dimming %s. CPU/GPU and emergency thermal protections are unchanged.\\n",\n             noDimOK ? "[OK]" : "[WARN]",\n             noDimOK ? "disabled for this SpringBoard session" : "could not be disabled on this build");\n    return YES;\n}\n'''
        text = replace_once(text, old, new, "Settings SpringBoard session hook")

    # A normal/manual Apply Tweaks run should have work even if no package is
    # pending, so this hard-off feature can stand on its own. Pending-only runs
    # are not forced to exploit just for this, but any SB session still gets the
    # re-assertion above.
    if "BOOL runThermalNoDim = !pendingOnly;" not in text:
        old = '''            BOOL runPowercuff = settings_enabled_tweak_should_run(d, kSettingsPowercuffEnabled, pendingOnly);\n            BOOL forceSpringBoardRefresh = runPowercuff &&\n'''
        new = '''            BOOL runPowercuff = settings_enabled_tweak_should_run(d, kSettingsPowercuffEnabled, pendingOnly);\n            BOOL runThermalNoDim = !pendingOnly;\n            BOOL forceSpringBoardRefresh = runPowercuff &&\n'''
        text = replace_once(text, old, new, "Settings runThermalNoDim declaration")

    old_needs = '''            BOOL needsSpringBoardWork = runSBC || runDarkTweaks || runStatBar || runNSBar || runNiceBarLite || runRSSI || runAxonLite || runGravityLite || runLayoutExtras || runTypeBanner || runNotificationIsland || runAppSwitcherGrid || runThemer || runSnowBoardLite || runLiveWP || runStageStrip || runFastLockXLite || runQuickLoader || runRepoTweaks || cleanupDisabledSpringBoardTweaks;\n'''
    if old_needs in text:
        new_needs = '''            BOOL needsSpringBoardWork = runThermalNoDim || runSBC || runDarkTweaks || runStatBar || runNSBar || runNiceBarLite || runRSSI || runAxonLite || runGravityLite || runLayoutExtras || runTypeBanner || runNotificationIsland || runAppSwitcherGrid || runThemer || runSnowBoardLite || runLiveWP || runStageStrip || runFastLockXLite || runQuickLoader || runRepoTweaks || cleanupDisabledSpringBoardTweaks;\n'''
        text = replace_once(text, old_needs, new_needs, "Settings needsSpringBoardWork")
    elif "BOOL needsSpringBoardWork = runThermalNoDim ||" not in text:
        raise RuntimeError("Settings needsSpringBoardWork anchor not found")

    if 'if (runThermalNoDim) [enabledTweaks addObject:@"no-thermal-dim"];' not in text:
        old = '''            if (runPowercuff) [enabledTweaks addObject:[NSString stringWithFormat:@"power(%@)", [d stringForKey:kSettingsPowercuffLevel] ?: @"nominal"]];\n'''
        new = old + '''            if (runThermalNoDim) [enabledTweaks addObject:@"no-thermal-dim"];\n'''
        text = replace_once(text, old, new, "Settings plan log")

    path.write_text(text, encoding="utf-8")
    print(f"[NO-DIM] patched {path}")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    header = root / "Cyanide" / "tweaks" / "powercuff.h"
    impl = root / "Cyanide" / "tweaks" / "powercuff.m"
    settings = root / "Cyanide" / "SettingsViewController.m"

    for p in (header, impl, settings):
        if not p.exists():
            raise FileNotFoundError(f"required source file missing: {p}")

    patch_header(header)
    patch_impl(impl)
    patch_settings(settings)
    print("[NO-DIM] patch complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
