#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


MARKER = "CYANIDE_POWERCUFF_THERMAL_CENTER_V3"


def fail(msg: str) -> None:
    raise RuntimeError(f"[POWERCUFF-V3] {msg}")


def replace_once_or_already(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"[POWERCUFF-V3] already patched: {label}")
        return text
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly 1 original match, found {count}")
    print(f"[POWERCUFF-V3] patched: {label}")
    return text.replace(old, new, 1)


def regex_once_or_already(text: str, pattern: str, replacement: str, label: str, already: str) -> str:
    if already in text:
        print(f"[POWERCUFF-V3] already patched: {label}")
        return text
    out, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        fail(f"{label}: expected exactly 1 regex match, found {count}")
    print(f"[POWERCUFF-V3] patched: {label}")
    return out


def patch_powercuff_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = "bool powercuff_apply(const char *level);\n"
    new = (
        "bool powercuff_apply(const char *level);\n"
        "\n"
        "// Unified Powercuff thermal-center display control. Call only while the\n"
        "// active RemoteCall target is SpringBoard. When disable=true, only the\n"
        "// 70/50/25%% thermal backlight mitigation thresholds are moved to the\n"
        "// emergency thermal threshold. CPU/GPU and emergency protections are not\n"
        "// modified. disable=false restores the captured original thresholds.\n"
        "bool powercuff_apply_thermal_dimming_in_session(bool disable);\n"
    )
    text = replace_once_or_already(text, old, new, "powercuff.h thermal-dimming API")
    path.write_text(text, encoding="utf-8")


def patch_powercuff_impl(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"[POWERCUFF-V3] implementation already patched: {path}")
        return

    old_imports = (
        '#import "powercuff.h"\n'
        '#import "remote_objc.h"\n'
        '#import "../TaskRop/RemoteCall.h"\n'
        '#import <stdio.h>\n'
        '#import <string.h>\n'
        '#import <unistd.h>\n'
        '#import "../LogTextView.h"\n'
    )
    new_imports = (
        '#import "powercuff.h"\n'
        '#import "remote_objc.h"\n'
        '#import "../TaskRop/RemoteCall.h"\n'
        '#import <Foundation/Foundation.h>\n'
        '#import <stdio.h>\n'
        '#import <string.h>\n'
        '#import <unistd.h>\n'
        '#import "../LogTextView.h"\n'
    )
    text = replace_once_or_already(text, old_imports, new_imports, "powercuff.m Foundation import")

    anchor = "static bool valid_level(const char *level) {\n"
    helper = r'''// CYANIDE_POWERCUFF_THERMAL_CENTER_V3
//
// Apple exposes the backlight thermal mitigations as separate behaviors.
// We touch only these three behaviors:
//   2 = 70% Backlight
//   4 = 50% Backlight
//   6 = 25% Backlight
// Emergency AppTerminate / DeviceRestart behaviors are used only as a safe
// upper threshold reference and are never rewritten.
enum {
    kPowercuffThermal70Backlight = 2,
    kPowercuffThermal50Backlight = 4,
    kPowercuffThermal25Backlight = 6,
    kPowercuffThermalAppTerminate = 8,
    kPowercuffThermalDeviceRestart = 9,
};

static NSString * const kPowercuffOrig70Key =
    @"PowercuffThermalCenterOriginal70";
static NSString * const kPowercuffOrig50Key =
    @"PowercuffThermalCenterOriginal50";
static NSString * const kPowercuffOrig25Key =
    @"PowercuffThermalCenterOriginal25";
static NSString * const kPowercuffOrigVersionKey =
    @"PowercuffThermalCenterOriginalVersion";

static NSString *powercuff_thermal_snapshot_version(void)
{
    NSOperatingSystemVersion v = [NSProcessInfo processInfo].operatingSystemVersion;
    return [NSString stringWithFormat:@"%ld.%ld.%ld",
            (long)v.majorVersion, (long)v.minorVersion, (long)v.patchVersion];
}

static int powercuff_thermal_level_for_behavior(int behavior)
{
    uint64_t raw = r_dlsym_call(R_TIMEOUT,
                                "_OSThermalNotificationLevelForBehavior",
                                (uint64_t)(uint32_t)behavior,
                                0, 0, 0, 0, 0, 0, 0);
    return (int)(int32_t)raw;
}

static bool powercuff_thermal_set_behavior_level(int behavior, int level)
{
    // Private ABI is (level, behavior). Both are int, so readback validation is
    // mandatory; a mismatch is treated as failure and callers roll back.
    (void)r_dlsym_call(R_TIMEOUT,
                       "_OSThermalNotificationSetLevelForBehavior",
                       (uint64_t)(int64_t)level,
                       (uint64_t)(uint32_t)behavior,
                       0, 0, 0, 0, 0, 0);
    usleep(20000);

    int readback = powercuff_thermal_level_for_behavior(behavior);
    printf("[POWERCUFF:DISPLAY] behavior=%d target=%d readback=%d\n",
           behavior, level, readback);
    return readback == level;
}

static bool powercuff_load_original_backlight_levels(int *l70, int *l50, int *l25)
{
    if (!l70 || !l50 || !l25) return false;

    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    NSString *savedVersion = [d stringForKey:kPowercuffOrigVersionKey];
    NSString *currentVersion = powercuff_thermal_snapshot_version();
    if (savedVersion.length == 0 || ![savedVersion isEqualToString:currentVersion]) {
        return false;
    }

    NSNumber *n70 = [d objectForKey:kPowercuffOrig70Key];
    NSNumber *n50 = [d objectForKey:kPowercuffOrig50Key];
    NSNumber *n25 = [d objectForKey:kPowercuffOrig25Key];
    if (![n70 isKindOfClass:NSNumber.class] ||
        ![n50 isKindOfClass:NSNumber.class] ||
        ![n25 isKindOfClass:NSNumber.class]) {
        return false;
    }

    *l70 = n70.intValue;
    *l50 = n50.intValue;
    *l25 = n25.intValue;
    return *l70 > 0 && *l50 > 0 && *l25 > 0;
}

static void powercuff_save_original_backlight_levels(int l70, int l50, int l25)
{
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    [d setInteger:l70 forKey:kPowercuffOrig70Key];
    [d setInteger:l50 forKey:kPowercuffOrig50Key];
    [d setInteger:l25 forKey:kPowercuffOrig25Key];
    [d setObject:powercuff_thermal_snapshot_version()
          forKey:kPowercuffOrigVersionKey];
    [d synchronize];
}

static bool powercuff_restore_backlight_levels(int l70, int l50, int l25)
{
    bool ok70 = powercuff_thermal_set_behavior_level(kPowercuffThermal70Backlight, l70);
    bool ok50 = powercuff_thermal_set_behavior_level(kPowercuffThermal50Backlight, l50);
    bool ok25 = powercuff_thermal_set_behavior_level(kPowercuffThermal25Backlight, l25);
    bool ok = ok70 && ok50 && ok25;
    printf("[POWERCUFF:DISPLAY] restore %s (%d/%d/%d)\n",
           ok ? "ok" : "failed", l70, l50, l25);
    return ok;
}

bool powercuff_apply_thermal_dimming_in_session(bool disable)
{
    int current70 = powercuff_thermal_level_for_behavior(kPowercuffThermal70Backlight);
    int current50 = powercuff_thermal_level_for_behavior(kPowercuffThermal50Backlight);
    int current25 = powercuff_thermal_level_for_behavior(kPowercuffThermal25Backlight);

    printf("[POWERCUFF:DISPLAY] current backlight levels 70=%d 50=%d 25=%d disable=%d\n",
           current70, current50, current25, disable);

    int original70 = 0, original50 = 0, original25 = 0;
    bool haveOriginal =
        powercuff_load_original_backlight_levels(&original70, &original50, &original25);

    if (!disable) {
        if (!haveOriginal) {
            // Nothing was captured by this OS-version build, therefore there
            // is nothing safe to restore. Treat the system-default request as
            // already satisfied instead of guessing threshold values.
            printf("[POWERCUFF:DISPLAY] no saved original thresholds; leaving system mapping unchanged\n");
            return true;
        }
        return powercuff_restore_backlight_levels(original70, original50, original25);
    }

    int restartLevel =
        powercuff_thermal_level_for_behavior(kPowercuffThermalDeviceRestart);
    int terminateLevel =
        powercuff_thermal_level_for_behavior(kPowercuffThermalAppTerminate);
    int disabledLevel = restartLevel > 0 ? restartLevel : terminateLevel;
    if (disabledLevel <= 0) {
        printf("[POWERCUFF:DISPLAY] no safe emergency threshold available; refusing to modify backlight behavior\n");
        return false;
    }

    if (!haveOriginal) {
        if (current70 <= 0 || current50 <= 0 || current25 <= 0) {
            printf("[POWERCUFF:DISPLAY] invalid original backlight levels; refusing to modify\n");
            return false;
        }

        // If all three already equal our target but no snapshot exists, we
        // cannot distinguish a previous modification from a genuine default.
        // Refuse to invent restore values.
        if (current70 == disabledLevel &&
            current50 == disabledLevel &&
            current25 == disabledLevel) {
            printf("[POWERCUFF:DISPLAY] backlight mapping already at target but no restore snapshot exists; refusing unsafe recapture\n");
            return false;
        }

        original70 = current70;
        original50 = current50;
        original25 = current25;
        powercuff_save_original_backlight_levels(original70, original50, original25);
        haveOriginal = true;
        printf("[POWERCUFF:DISPLAY] captured originals 70=%d 50=%d 25=%d for iOS %s\n",
               original70, original50, original25,
               [powercuff_thermal_snapshot_version() UTF8String]);
    }

    printf("[POWERCUFF:DISPLAY] moving 70/50/25%% backlight mitigations to emergency level %d (terminate=%d restart=%d)\n",
           disabledLevel, terminateLevel, restartLevel);

    bool ok70 =
        powercuff_thermal_set_behavior_level(kPowercuffThermal70Backlight, disabledLevel);
    bool ok50 =
        powercuff_thermal_set_behavior_level(kPowercuffThermal50Backlight, disabledLevel);
    bool ok25 =
        powercuff_thermal_set_behavior_level(kPowercuffThermal25Backlight, disabledLevel);
    bool ok = ok70 && ok50 && ok25;

    if (!ok) {
        printf("[POWERCUFF:DISPLAY] partial/mismatched write; rolling back original thresholds\n");
        if (haveOriginal) {
            (void)powercuff_restore_backlight_levels(original70, original50, original25);
        }
        return false;
    }

    printf("[POWERCUFF:DISPLAY] thermal screen dimming disabled; emergency thermal behaviors unchanged\n");
    return true;
}

'''
    text = replace_once_or_already(
        text, anchor, helper + anchor,
        "powercuff.m unified display thermal backend"
    )
    path.write_text(text, encoding="utf-8")


def patch_settings(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_keys = (
        'NSString * const kSettingsPowercuffEnabled = @"PowercuffEnabled";\n'
        'NSString * const kSettingsPowercuffLevel   = @"PowercuffLevel";\n'
        'static NSString * const kSettingsPowercuffNominalNoticeShown = @"cyanide.powercuff.nominalDefaultNoticeShown.v1";\n'
    )
    new_keys = (
        'NSString * const kSettingsPowercuffEnabled = @"PowercuffEnabled";\n'
        'NSString * const kSettingsPowercuffLevel   = @"PowercuffLevel";\n'
        'static NSString * const kSettingsPowercuffCPUThrottleEnabled = @"PowercuffCPUThrottleEnabled";\n'
        'static NSString * const kSettingsPowercuffDisableThermalDimming = @"PowercuffDisableThermalDimming";\n'
        'static NSString * const kSettingsPowercuffNominalNoticeShown = @"cyanide.powercuff.nominalDefaultNoticeShown.v1";\n'
    )
    text = replace_once_or_already(text, old_keys, new_keys, "Settings Powercuff config keys")

    old_levels = (
        'static NSArray<NSString *> *powercuff_levels(void) {\n'
        '    return @[ @"off", @"nominal", @"light", @"moderate", @"heavy" ];\n'
        '}\n'
    )
    new_levels = (
        'static NSArray<NSString *> *powercuff_levels(void) {\n'
        '    // Stored machine values. "off" moved to the independent CPU/GPU switch.\n'
        '    return @[ @"nominal", @"light", @"moderate", @"heavy" ];\n'
        '}\n'
        '\n'
        'static NSArray<NSString *> *powercuff_level_titles(void) {\n'
        '    return @[ @"正常", @"轻度", @"中度", @"重度" ];\n'
        '}\n'
        '\n'
        'static NSString *powercuff_level_display_name(NSString *level) {\n'
        '    NSUInteger idx = [powercuff_levels() indexOfObject:level ?: @""];\n'
        '    if (idx == NSNotFound || idx >= powercuff_level_titles().count) return @"正常";\n'
        '    return powercuff_level_titles()[idx];\n'
        '}\n'
    )
    text = replace_once_or_already(text, old_levels, new_levels, "Settings Powercuff level model")

    old_rows = '''- (NSArray<NSDictionary *> *)powercuffRows
{
    return @[
        @{ @"kind": @"segmented", @"key": kSettingsPowercuffLevel,   @"title": @"Level" },
    ];
}
'''
    new_rows = '''- (NSArray<NSDictionary *> *)powercuffRows
{
    return @[
        @{ @"kind": @"toggle",
           @"key": kSettingsPowercuffCPUThrottleEnabled,
           @"title": @"CPU / GPU 性能限制",
           @"subtitle": @"开启后按下方等级主动模拟热压力并限制 CPU/GPU 性能。" },
        @{ @"kind": @"segmented",
           @"key": kSettingsPowercuffLevel,
           @"title": @"性能等级" },
        @{ @"kind": @"toggle",
           @"key": kSettingsPowercuffDisableThermalDimming,
           @"title": @"禁止热降亮",
           @"subtitle": @"阻止 70% / 50% / 25% 系统热背光限制；紧急热保护仍由 iOS 保留。" },
    ];
}
'''
    text = replace_once_or_already(text, old_rows, new_rows, "Settings Powercuff rows")

    old_seg = '''        UISegmentedControl *seg = [[UISegmentedControl alloc] initWithItems:powercuff_levels()];
        seg.translatesAutoresizingMaskIntoConstraints = NO;
        NSString *cur = [d stringForKey:row[@"key"]] ?: @"nominal";
        NSUInteger idx = [powercuff_levels() indexOfObject:cur];
        if (idx == NSNotFound) idx = [powercuff_levels() indexOfObject:@"nominal"];
        seg.selectedSegmentIndex = (NSInteger)idx;
        seg.enabled = supported;
'''
    new_seg = '''        UISegmentedControl *seg = [[UISegmentedControl alloc] initWithItems:powercuff_level_titles()];
        seg.translatesAutoresizingMaskIntoConstraints = NO;
        NSString *cur = [d stringForKey:row[@"key"]] ?: @"nominal";
        NSUInteger idx = [powercuff_levels() indexOfObject:cur];
        if (idx == NSNotFound) idx = [powercuff_levels() indexOfObject:@"nominal"];
        seg.selectedSegmentIndex = (NSInteger)idx;
        seg.enabled = supported && [d boolForKey:kSettingsPowercuffCPUThrottleEnabled];
'''
    text = replace_once_or_already(text, old_seg, new_seg, "Settings Powercuff segmented titles/enable state")

    old_defaults = '''        kSettingsPowercuffEnabled: @NO,
        kSettingsPowercuffLevel:   @"nominal",
'''
    new_defaults = '''        kSettingsPowercuffEnabled: @NO,
        kSettingsPowercuffCPUThrottleEnabled: @NO,
        kSettingsPowercuffLevel:   @"nominal",
        kSettingsPowercuffDisableThermalDimming: @YES,
'''
    text = replace_once_or_already(text, old_defaults, new_defaults, "Settings Powercuff defaults")

    note_anchor = '''static void settings_note_package_configuration_changed(NSString *key)
{
'''
    note_insert = '''static void settings_note_package_configuration_changed(NSString *key)
{
    if ([key isEqualToString:kSettingsPowercuffCPUThrottleEnabled] ||
        [key isEqualToString:kSettingsPowercuffLevel] ||
        [key isEqualToString:kSettingsPowercuffDisableThermalDimming]) {
        settings_mark_tweak_needs_apply(kSettingsPowercuffEnabled);
        settings_notify_package_queue_changed_async();
        return;
    }
'''
    text = replace_once_or_already(
        text, note_anchor, note_insert,
        "Settings Powercuff config marks package pending"
    )

    old_run_decl = '''            BOOL runPowercuff = settings_enabled_tweak_should_run(d, kSettingsPowercuffEnabled, pendingOnly);
            BOOL forceSpringBoardRefresh = runPowercuff &&
'''
    new_run_decl = '''            BOOL runPowercuff = settings_enabled_tweak_should_run(d, kSettingsPowercuffEnabled, pendingOnly);
            BOOL powercuffCPUApplyOK = YES;
            BOOL forceSpringBoardRefresh = runPowercuff &&
'''
    text = replace_once_or_already(text, old_run_decl, new_run_decl, "Settings Powercuff run state")

    old_needs = '''            BOOL needsSpringBoardWork = runSBC || runDarkTweaks || runStatBar || runNSBar || runNiceBarLite || runRSSI || runAxonLite || runGravityLite || runLayoutExtras || runTypeBanner || runNotificationIsland || runAppSwitcherGrid || runThemer || runSnowBoardLite || runLiveWP || runStageStrip || runFastLockXLite || runQuickLoader || runRepoTweaks || cleanupDisabledSpringBoardTweaks;
'''
    new_needs = '''            BOOL needsSpringBoardWork = runPowercuff || runSBC || runDarkTweaks || runStatBar || runNSBar || runNiceBarLite || runRSSI || runAxonLite || runGravityLite || runLayoutExtras || runTypeBanner || runNotificationIsland || runAppSwitcherGrid || runThemer || runSnowBoardLite || runLiveWP || runStageStrip || runFastLockXLite || runQuickLoader || runRepoTweaks || cleanupDisabledSpringBoardTweaks;
'''
    text = replace_once_or_already(text, old_needs, new_needs, "Settings Powercuff requires SpringBoard stage")

    old_cpu = '''                    NSString *lvl = [d stringForKey:kSettingsPowercuffLevel] ?: @"nominal";
                    bool ok = powercuff_apply(lvl.UTF8String);
                    settings_mark_tweak_applied(kSettingsPowercuffEnabled,
                                                ok && [d boolForKey:kSettingsPowercuffEnabled]);
                    log_user("%s Powercuff %s through thermalmonitord.\\n",
                             ok ? "[OK]" : "[WARN]",
                             ok ? "applied" : "did not apply cleanly");
                    cyanide_upload_log_milestone(ok ? @"powercuff-applied" : @"powercuff-failed");
'''
    new_cpu = '''                    BOOL cpuThrottleEnabled = [d boolForKey:kSettingsPowercuffCPUThrottleEnabled];
                    NSString *configuredLevel = [d stringForKey:kSettingsPowercuffLevel] ?: @"nominal";
                    if (![powercuff_levels() containsObject:configuredLevel]) configuredLevel = @"nominal";
                    const char *effectiveLevel = cpuThrottleEnabled ? configuredLevel.UTF8String : "off";
                    bool ok = powercuff_apply(effectiveLevel);
                    powercuffCPUApplyOK = ok;
                    settings_mark_tweak_applied(kSettingsPowercuffEnabled,
                                                ok && [d boolForKey:kSettingsPowercuffEnabled]);
                    log_user("%s Powercuff CPU/GPU control %s (%s).\\n",
                             ok ? "[OK]" : "[WARN]",
                             cpuThrottleEnabled ? (ok ? "applied" : "failed")
                                                : (ok ? "disabled" : "failed to disable"),
                             effectiveLevel);
                    cyanide_upload_log_milestone(ok ? @"powercuff-cpu-applied" : @"powercuff-cpu-failed");
'''
    text = replace_once_or_already(text, old_cpu, new_cpu, "Settings Powercuff optional CPU/GPU stage")

    sb_anchor = '''                    log_user("[OK] SpringBoard channel open.\\n");
                    cyanide_upload_log_milestone(@"springboard-remote-call-ready");

                    if (runSandboxEscape && !g_springboard_sandbox_escaped) {
'''
    sb_new = '''                    log_user("[OK] SpringBoard channel open.\\n");
                    cyanide_upload_log_milestone(@"springboard-remote-call-ready");

                    if (runPowercuff) {
                        BOOL disableThermalDimming =
                            [d boolForKey:kSettingsPowercuffDisableThermalDimming];
                        bool displayOK =
                            powercuff_apply_thermal_dimming_in_session(disableThermalDimming);
                        bool combinedOK = powercuffCPUApplyOK && displayOK;
                        settings_mark_tweak_applied(kSettingsPowercuffEnabled,
                                                    combinedOK && [d boolForKey:kSettingsPowercuffEnabled]);
                        log_user("%s Powercuff 屏幕热降亮：%s。系统紧急热保护保持不变。\\n",
                                 displayOK ? "[OK]" : "[WARN]",
                                 disableThermalDimming
                                     ? (displayOK ? "已禁用" : "禁用失败")
                                     : (displayOK ? "系统默认" : "恢复失败"));
                        cyanide_upload_log_milestone(displayOK
                            ? (disableThermalDimming ? @"powercuff-display-dim-disabled"
                                                     : @"powercuff-display-dim-restored")
                            : @"powercuff-display-dim-failed");
                    }

                    if (runSandboxEscape && !g_springboard_sandbox_escaped) {
'''
    text = replace_once_or_already(text, sb_anchor, sb_new, "Settings Powercuff SpringBoard display stage")

    old_summary = '''    } else if (section == SectionPowercuff) {
        NSString *lvl = [d stringForKey:kSettingsPowercuffLevel] ?: @"nominal";
        [out addObject:@{@"title": @"Level", @"value": lvl}];
    } else if (section == SectionDragCoefficient) {
'''
    new_summary = '''    } else if (section == SectionPowercuff) {
        BOOL cpuThrottle = [d boolForKey:kSettingsPowercuffCPUThrottleEnabled];
        NSString *lvl = [d stringForKey:kSettingsPowercuffLevel] ?: @"nominal";
        BOOL noDim = [d boolForKey:kSettingsPowercuffDisableThermalDimming];
        [out addObject:@{@"title": @"CPU / GPU 性能限制",
                         @"value": cpuThrottle ? @"已开启" : @"已关闭"}];
        [out addObject:@{@"title": @"性能等级",
                         @"value": powercuff_level_display_name(lvl)}];
        [out addObject:@{@"title": @"屏幕热降亮",
                         @"value": noDim ? @"已禁用" : @"系统默认"}];
        [out addObject:@{@"title": @"系统紧急热保护",
                         @"value": @"保留"}];
    } else if (section == SectionDragCoefficient) {
'''
    text = replace_once_or_already(text, old_summary, new_summary, "Settings Powercuff Chinese current-settings summary")

    old_notice_call = '''    [self presentPowercuffNominalNoticeIfNeeded];
'''
    new_notice_call = '''    // Powercuff v3 has independent controls; the old Nominal-only notice is obsolete.
'''
    text = replace_once_or_already(text, old_notice_call, new_notice_call, "Settings remove obsolete Powercuff nominal notice")

    old_seg_changed = r'''- (void)powercuffSegChanged:(UISegmentedControl *)sender
{
    if (!settings_device_supported()) {
        printf("[SETTINGS] powercuff level blocked: %s\n", settings_unsupported_message().UTF8String);
        return;
    }

    NSArray<NSString *> *levels = powercuff_levels();
    if (sender.selectedSegmentIndex < 0 || sender.selectedSegmentIndex >= (NSInteger)levels.count) return;
    [[NSUserDefaults standardUserDefaults] setObject:levels[sender.selectedSegmentIndex]
                                              forKey:kSettingsPowercuffLevel];
}
'''
    new_seg_changed = r'''- (void)powercuffSegChanged:(UISegmentedControl *)sender
{
    if (!settings_device_supported()) {
        printf("[SETTINGS] powercuff level blocked: %s\n", settings_unsupported_message().UTF8String);
        return;
    }

    NSArray<NSString *> *levels = powercuff_levels();
    if (sender.selectedSegmentIndex < 0 || sender.selectedSegmentIndex >= (NSInteger)levels.count) return;

    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    [d setObject:levels[sender.selectedSegmentIndex]
          forKey:kSettingsPowercuffLevel];
    [d synchronize];
    settings_note_package_configuration_changed(kSettingsPowercuffLevel);
    [self reloadSectionOrAll:SectionPowercuff];
}
'''
    text = replace_once_or_already(
        text, old_seg_changed, new_seg_changed,
        "Settings Powercuff segmented change tracking"
    )

    toggle_old = '''    settings_schedule_live_apply_for_key(key);
    [self presentApplyLogIfRunning];
}

- (void)sliderChanged:(UISlider *)sender
'''
    toggle_new = '''    settings_schedule_live_apply_for_key(key);
    if ([key isEqualToString:kSettingsPowercuffCPUThrottleEnabled]) {
        [self reloadSectionOrAll:SectionPowercuff];
    }
    [self presentApplyLogIfRunning];
}

- (void)sliderChanged:(UISlider *)sender
'''
    text = replace_once_or_already(text, toggle_old, toggle_new, "Settings Powercuff CPU switch refresh")

    path.write_text(text, encoding="utf-8")


def patch_package_catalog(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''        Package *powercuff = [[Package alloc] initWithIdentifier:@"com.darksword.powercuff"
                                           name:@"Powercuff"
                               shortDescription:@"Underclock the CPU/GPU thermal pressure"
                                longDescription:@"Drives thermalmonitord with synthetic thermal pressure to underclock the CPU and GPU. Useful for cooling-sensitive workloads or extending runtime under load. Effects persist until reboot.\\n\\nNominal is the daily-use default. Light, Moderate, and Heavy intentionally underclock the CPU more, so lag and slower app launches mean it is working as intended. Those levels can be too slow for comfortable day-to-day use, especially on older devices.\\n\\nPick a level in the Settings tab."
                                        version:version
'''
    new = '''        Package *powercuff = [[Package alloc] initWithIdentifier:@"com.darksword.powercuff"
                                           name:@"Powercuff"
                               shortDescription:@"CPU/GPU throttling and thermal screen-dimming controls"
                                longDescription:@"Unified thermal controls. CPU/GPU synthetic thermal throttling can be enabled independently and set to Nominal, Light, Moderate, or Heavy. Thermal screen dimming can also be disabled independently by moving only the 70%, 50%, and 25% backlight mitigation thresholds to the emergency thermal threshold.\\n\\nCPU/GPU system thermal protection, flashlight protection, app termination, and device-restart emergency thermal behavior remain under iOS control."
                                        version:version
'''
    text = replace_once_or_already(text, old, new, "PackageCatalog Powercuff description")
    path.write_text(text, encoding="utf-8")


def patch_zh(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''        "Powercuff": (
            "通过热压力等级限制 CPU / GPU 性能",
            "模拟 thermalmonitord 压力等级以限制 CPU/GPU 性能，可选择 nominal、light、moderate、heavy 等级，效果持续到重启。",
        ),
'''
    new = '''        "Powercuff": (
            "CPU/GPU 降频与屏幕热降亮控制",
            "统一管理温控：CPU/GPU 性能限制可以独立开关，并可选择正常、轻度、中度、重度；屏幕热降亮也可独立禁用，仅调整 70% / 50% / 25% 背光热缓解阈值。CPU/GPU 系统温控、闪光灯限制以及 App 终止/设备重启等紧急热保护仍由 iOS 保留。",
        ),
'''
    text = replace_once_or_already(text, old, new, "Simplified Chinese Powercuff package metadata")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    required = [
        root / "Cyanide" / "tweaks" / "powercuff.h",
        root / "Cyanide" / "tweaks" / "powercuff.m",
        root / "Cyanide" / "SettingsViewController.m",
        root / "Cyanide" / "installer" / "PackageCatalog.m",
        root / "apply_zh_hans.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        fail("required source file(s) missing: " + ", ".join(missing))

    patch_powercuff_header(required[0])
    patch_powercuff_impl(required[1])
    patch_settings(required[2])
    patch_package_catalog(required[3])
    patch_zh(required[4])

    print("[POWERCUFF-V3] complete")
    print("[POWERCUFF-V3] runtime package remains a single Powercuff package; no separate No-Dim package is created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
