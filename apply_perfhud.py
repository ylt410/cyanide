#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(f"[PerfHUD] {message}")


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    if new in text and old not in text:
        print(f"[PerfHUD] {label}: already patched")
        return text
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one original block, found {count}. No files were changed.")
    print(f"[PerfHUD] {label}: OK")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    if not (root / "Cyanide.xcodeproj").exists() or not (root / "Cyanide").is_dir():
        fail("run this from the Cyanide repository root, or pass the repo path as the first argument")

    new_statbar = SCRIPT_DIR / "statbar.m"
    if not new_statbar.exists():
        fail(f"missing {new_statbar.name} next to this script")

    settings_path = root / "Cyanide" / "SettingsViewController.m"
    catalog_path = root / "Cyanide" / "installer" / "PackageCatalog.m"
    statbar_path = root / "Cyanide" / "tweaks" / "statbar.m"

    for path in (settings_path, catalog_path, statbar_path):
        if not path.exists():
            fail(f"missing expected file: {path.relative_to(root)}")

    settings = settings_path.read_text(encoding="utf-8")
    catalog = catalog_path.read_text(encoding="utf-8")

    old_rows = '''- (NSArray<NSDictionary *> *)statbarRows
{
    return @[
        @{ @"kind": @"toggle", @"key": kSettingsStatBarCelsius,     @"title": @"Celsius" },
        @{ @"kind": @"toggle", @"key": kSettingsStatBarShowCPU,     @"title": @"Show CPU %" },
        @{ @"kind": @"toggle", @"key": kSettingsStatBarShowLabels,  @"title": @"Show CPU / RAM labels" },
        @{ @"kind": @"toggle", @"key": kSettingsStatBarShowNet,     @"title": @"Show network speed" },
        @{ @"kind": @"toggle", @"key": kSettingsStatBarNetworkOnly, @"title": @"Network speed only" },
        @{ @"kind": @"slider", @"key": kSettingsStatBarRefreshRateSec,
           @"title": @"Refresh rate", @"min": @1, @"max": @30, @"step": @1,
           @"unit": @"s", @"default": @(kStatBarDefaultRefreshRateSec) },
    ];
}'''

    new_rows = '''- (NSArray<NSDictionary *> *)statbarRows
{
    return @[
        @{ @"kind": @"slider", @"key": kSettingsStatBarRefreshRateSec,
           @"title": @"Refresh rate", @"min": @1, @"max": @30, @"step": @1,
           @"unit": @"s", @"default": @(kStatBarDefaultRefreshRateSec) },
    ];
}'''

    old_summary = '''        [out addObject:@{@"title": @"Celsius",             @"value": [d boolForKey:kSettingsStatBarCelsius]    ? @"On" : @"Off"}];
        [out addObject:@{@"title": @"Show CPU %",          @"value": [d boolForKey:kSettingsStatBarShowCPU]    ? @"On" : @"Off"}];
        [out addObject:@{@"title": @"Show CPU/RAM labels", @"value": [d boolForKey:kSettingsStatBarShowLabels] ? @"On" : @"Off"}];
        [out addObject:@{@"title": @"Show net speed",      @"value": [d boolForKey:kSettingsStatBarShowNet]    ? @"On" : @"Off"}];
        [out addObject:@{@"title": @"Network speed only",  @"value": [d boolForKey:kSettingsStatBarNetworkOnly] ? @"On" : @"Off"}];
        [out addObject:@{@"title": @"Refresh rate",        @"value": [NSString stringWithFormat:@"%lds",
                                                                       (long)[d integerForKey:kSettingsStatBarRefreshRateSec]]}];'''

    new_summary = '''        [out addObject:@{@"title": @"Metrics",             @"value": @"CPU / GPU / RAM"}];
        [out addObject:@{@"title": @"Background",          @"value": @"Transparent"}];
        [out addObject:@{@"title": @"Refresh rate",        @"value": [NSString stringWithFormat:@"%lds",
                                                                       (long)[d integerForKey:kSettingsStatBarRefreshRateSec]]}];'''

    old_bundle_row = '''        @{ @"title": @"StatBar",            @"icon": @"thermometer.medium",                  @"color": [UIColor systemRedColor],    @"section": @(SectionStatBar) },'''
    new_bundle_row = '''        @{ @"title": @"PerfHUD",            @"icon": @"speedometer",                        @"color": [UIColor systemOrangeColor], @"section": @(SectionStatBar) },'''

    old_footer = '''    if (s == SectionStatBar) {
        return @"Live overlay. When enabled, StatBar keeps a SpringBoard RemoteCall session open. Refresh rate applies when Cyanide is minimized but the screen is still awake; StatBar pauses while the screen is locked or asleep.";
    }'''
    new_footer = '''    if (s == SectionStatBar) {
        return @"Transparent CPU / GPU / RAM performance HUD. Each metric changes color with load. GPU uses IOKit statistics when available and safely shows -- when the current device/build does not expose a usable utilization value. The HUD pauses while the screen is locked or asleep.";
    }'''

    old_catalog = '''        Package *statBar = [[Package alloc] initWithIdentifier:@"com.darksword.statbar"
                                           name:@"StatBar"
                               shortDescription:@"Battery temperature + free RAM overlay"
                                longDescription:@"Installs an overlay window in SpringBoard that shows live battery temperature and free RAM next to the system status bar. Refresh timing is adjustable so you can trade live updates for battery life.\\n\\nConfigure units, visible metrics, and refresh speed in the Settings tab."
                                        version:version
                                         author:@"zeroxjf"
                                       category:@"Status Bar"
                                     symbolName:@"thermometer.medium"
                                           kind:PackageInstallKindToggle
                                     enabledKey:kSettingsStatBarEnabled
                                          isNew:NO];
        statBar.settingsSection = kSecStatBar;'''

    new_catalog = '''        Package *statBar = [[Package alloc] initWithIdentifier:@"com.darksword.statbar"
                                           name:@"PerfHUD"
                               shortDescription:@"Live CPU / GPU / RAM overlay"
                                longDescription:@"Shows system-wide CPU, GPU, and RAM utilization in a transparent SpringBoard overlay. Each metric changes color as load rises: green at normal load, yellow/orange under heavier load, and red near saturation.\\n\\nGPU utilization is read from IOKit PerformanceStatistics when available. Unsupported device/build combinations safely show GPU -- instead of failing the whole HUD. Refresh timing is adjustable in Settings."
                                        version:version
                                         author:@"zeroxjf / custom PerfHUD fork"
                                       category:@"Performance"
                                     symbolName:@"speedometer"
                                           kind:PackageInstallKindToggle
                                     enabledKey:kSettingsStatBarEnabled
                                          isNew:NO];
        statBar.settingsSection = kSecStatBar;'''

    # Validate every edit in memory before touching the repository.
    settings_new = replace_exact(settings, old_rows, new_rows, "Settings: PerfHUD rows")
    settings_new = replace_exact(settings_new, old_summary, new_summary, "Settings: package summary")
    settings_new = replace_exact(settings_new, old_bundle_row, new_bundle_row, "Settings: bundle title")
    settings_new = replace_exact(settings_new, old_footer, new_footer, "Settings: footer")
    catalog_new = replace_exact(catalog, old_catalog, new_catalog, "PackageCatalog: PerfHUD package")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f".perfhud-backup-{stamp}"
    (backup / "Cyanide" / "tweaks").mkdir(parents=True, exist_ok=True)
    (backup / "Cyanide" / "installer").mkdir(parents=True, exist_ok=True)
    shutil.copy2(statbar_path, backup / "Cyanide" / "tweaks" / "statbar.m")
    shutil.copy2(settings_path, backup / "Cyanide" / "SettingsViewController.m")
    shutil.copy2(catalog_path, backup / "Cyanide" / "installer" / "PackageCatalog.m")

    statbar_path.write_text(new_statbar.read_text(encoding="utf-8"), encoding="utf-8")
    settings_path.write_text(settings_new, encoding="utf-8")
    catalog_path.write_text(catalog_new, encoding="utf-8")

    print(f"[PerfHUD] patched successfully")
    print(f"[PerfHUD] backup: {backup}")
    print("[PerfHUD] next: ./scripts/build.sh")


if __name__ == "__main__":
    main()
