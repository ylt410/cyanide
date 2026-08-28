#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def fail(msg: str) -> None:
    raise SystemExit(f"[ZH] {msg}")


def objc(s: str) -> str:
    return '@"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def patch_literals(root: Path, rel: str, mapping: dict[str, str], required: tuple[str, ...] = ()) -> int:
    path = root / rel
    if not path.exists():
        print(f"[ZH] skip missing: {rel}")
        return 0
    text = path.read_text(encoding="utf-8")
    original = text
    for anchor in required:
        if anchor not in text:
            fail(f"{rel}: required anchor not found: {anchor}")
    total = 0
    for old, new in mapping.items():
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            total += count
    if text != original:
        path.write_text(text, encoding="utf-8")
    print(f"[ZH] {rel}: {total} replacements")
    return total


def localize_package_catalog(root: Path) -> int:
    path = root / "Cyanide/installer/PackageCatalog.m"
    if not path.exists():
        print("[ZH] skip missing PackageCatalog.m")
        return 0
    text = path.read_text(encoding="utf-8")
    original = text

    descriptions: dict[str, tuple[str, str]] = {
        "PerfHUD": (
            "实时 CPU / GPU / 内存透明悬浮监控",
            "在 SpringBoard 上显示系统 CPU、GPU 和内存实时占用。悬浮窗会跟随前台 App 的实际 UI 朝向适配横竖屏，竖屏时自动避开灵动岛，横屏时保持顶部居中；不使用背景或文字阴影，并根据每项负载独立变色。GPU 数据不可用时会安全显示 GPU --。刷新间隔可在 0.25～5.00 秒之间调整。",
        ),
        "App Switcher Grid": (
            "将多任务切换器改成网格式布局",
            "把系统 App Switcher 调整为更紧凑的网格视图。属于 SpringBoard 运行时修改，不改变应用本身。",
        ),
        "Axon Lite": (
            "按应用分组通知中心请求",
            "在通知中心中按应用整理通知请求，并在 RemoteCall 会话存活期间去除重复项。",
        ),
        "Call Recording Sound": (
            "静音或恢复通话录音提示音",
            "对 CallServices 的通话录音提示音文件执行静音或恢复操作。首次修改会尽量备份原文件；此功能属于持久系统文件修改。",
        ),
        "Disable App Library": (
            "移除主屏幕末尾的 App 资源库页面",
            "隐藏主屏幕最后一页之后的 App 资源库入口。停用后可恢复系统默认行为。",
        ),
        "Disable Icon Fly-In": (
            "关闭图标飞入动画",
            "跳过 SpringBoard 图标出现时的弹入动画，让主屏幕显示更直接。",
        ),
        "Double-Tap to Lock": (
            "双击壁纸锁定设备",
            "在主屏幕壁纸区域双击即可锁定设备。",
        ),
        "Drag Coefficient": (
            "调整 SpringBoard 动画速度倍率",
            "修改 SpringBoard 动画时间倍率，用于加快或减慢系统界面动画。",
        ),
        "Dynamic Stage Lite": (
            "在 SpringBoard 上运行两个悬浮应用窗口",
            "实验性的双窗口场景托管实现，可移动和缩放两个应用窗口。当前版本的托管窗口触摸路由仍不完整。",
        ),
        "FastLockX Lite": (
            "增强 Face ID 重试与解锁流程",
            "通过 SpringBoard 定时器保持部分 Face ID 重试和解锁请求处于可用状态。",
        ),
        "Gravity Lite": (
            "让主屏幕图标加入物理效果",
            "为主屏幕和 Dock 图标加入重力、碰撞、弹性和加速度计方向等物理效果。",
        ),
        "Hide Home Bar": (
            "隐藏底部 Home 指示条",
            "隐藏系统底部 Home 指示条。该功能需要单独执行，并可能在修改后要求 Respring。",
        ),
        "Home Layout Extras": (
            "额外主屏幕间距与图标缩放设置",
            "提供主屏幕和 Dock 的额外边距、间距与每个图标的缩放控制，可与 SBCustomizer 配合使用。",
        ),
        "IPA Decryptor": (
            "实验性本地 IPA 解密流程，尚未完成",
            "当前仅完成应用发现、App Store 解析、登录、加密 IPA 获取和加密信息探测等前半部分。补丁、页面导出与 Payload IPA 重建仍未完成，因此安装被禁用。",
        ),
        "LiveWP": (
            "使用视频作为主屏幕/锁屏壁纸",
            "把选定的视频复制到 Cyanide 容器，并在 RemoteCall 会话存活期间播放在 SpringBoard 主屏幕和锁屏窗口之后。",
        ),
        "Location Simulator": (
            "模拟静态 CoreLocation 坐标",
            "通过系统 CoreLocation 模拟路径设置静态位置。部分应用或服务可能限制或禁止模拟位置，请只在有权限的场景使用。",
        ),
        "NiceBar Lite": (
            "可自定义的状态栏信息标签",
            "在状态栏附近显示日期、电量、内存、网络、运行时间、IP、磁盘、温度状态等可配置信息。",
        ),
        "Notification Island": (
            "实验性灵动岛通知镜像，尚未完成",
            "尝试把通知内容镜像到 Dynamic Island 风格界面。当前仍为未完成实验项目，因此安装被禁用。",
        ),
        "NSBar": (
            "在状态栏显示实时上下行网速",
            "提供紧凑的实时下载/上传速度显示，并支持选择状态栏附近的位置。",
        ),
        "OTA Updates": (
            "启用或禁用系统 OTA 更新提示",
            "通过系统 OTA 相关 launchd 配置切换更新提示。该修改会跨重启保留，使用时要清楚自己是否仍需要系统安全更新。",
        ),
        "Powercuff": (
            "通过热压力等级限制 CPU / GPU 性能",
            "模拟 thermalmonitord 压力等级以限制 CPU/GPU 性能，可选择 nominal、light、moderate、heavy 等级，效果持续到重启。",
        ),
        "QuickLoader": (
            "运行本地 JavaScript 插件文件",
            "从“文件”中选择本地 .js 插件，并通过 Cyanide 的 JavaScriptCore 与 RemoteCall bridge 在 SpringBoard 中运行。仅运行你信任的脚本。",
        ),
        "SBCustomizer": (
            "自定义 Dock 数量与主屏幕网格",
            "调整 Dock 图标数量、主屏幕列数/行数，并可隐藏图标名称。",
        ),
        "Signal Readouts": (
            "实验性数字信号强度显示，尚未完成",
            "用于在状态栏附近显示蜂窝/Wi‑Fi 信号数值的实验功能，目前仍未完成，因此安装被禁用。",
        ),
        "SnowBoard Lite": (
            "导入并应用 SnowBoard 风格图标主题",
            "把 SnowBoard/IconBundles 风格主题文件夹或压缩包导入 Cyanide 本地主题库，再通过现有图标替换流程应用。",
        ),
        "TypeBanner": (
            "实验性 iMessage 正在输入横幅，尚未完成",
            "尝试把 iMessage 正在输入状态显示为系统横幅。当前仍为未完成实验项目，因此安装被禁用。",
        ),
        "Watch Pairing Override": (
            "覆盖 Apple Watch 配对版本范围",
            "修改 iPhone 保存的 watchOS 配对范围，用于尝试配对更新或更旧的 Apple Watch。修改会跨重启保留，配对前通常需要 Respring。",
        ),
        "Zero Backlight Fade": (
            "移除锁定/唤醒时的背光渐变",
            "让锁定和唤醒时的背光变化更直接，跳过默认渐变过程。",
        ),
        "Zero Wake Animation": (
            "移除唤醒动画",
            "让屏幕唤醒时直接显示，跳过默认唤醒动画。",
        ),
        "Cyanide Themer": (
            "按 Bundle ID 替换应用图标",
            "遍历 SpringBoard 图标视图，并按应用 Bundle ID 使用本地图像替换图标。",
        ),
    }

    replaced = 0
    string_pat = r'@"(?:\\.|[^"\\])*"'
    for name, (short, long) in descriptions.items():
        pattern = re.compile(
            r'(name:\s*@"' + re.escape(name) + r'"\s*\n?\s*shortDescription:)\s*' + string_pat +
            r'(\s*\n?\s*longDescription:)\s*' + string_pat,
            re.S,
        )
        m = pattern.search(text)
        if not m:
            continue
        replacement = m.group(1) + " " + objc(short) + m.group(2) + " " + objc(long)
        text = text[:m.start()] + replacement + text[m.end():]
        replaced += 1

    # PerfHUD custom entry is known and worth localizing even if formatting differs.
    text = text.replace(objc("Live CPU / GPU / RAM overlay"), objc("实时 CPU / GPU / 内存透明悬浮监控"))
    text = text.replace(
        objc("Shows system-wide CPU, GPU, and RAM utilization in a transparent SpringBoard overlay. The HUD follows the frontmost app's UI orientation, stays below the Dynamic Island in portrait, uses no background or text shadow, and colors each metric independently as load rises.\n\nGPU utilization is read from IOKit PerformanceStatistics when available. Unsupported device/build combinations safely show GPU --. Refresh interval is adjustable from 0.25 to 5.00 seconds in Settings."),
        objc("在 SpringBoard 上以透明悬浮窗显示系统 CPU、GPU 和内存占用。悬浮窗会跟随前台 App 的实际 UI 朝向适配横竖屏，竖屏时自动避开灵动岛，不使用背景或文字阴影，并根据每项负载独立变色。\n\nGPU 会优先读取 IOKit PerformanceStatistics；当前设备或系统构建无法提供可用数据时会安全显示 GPU --。刷新间隔可在设置中调整为 0.25～5.00 秒。"),
    )

    if text != original:
        path.write_text(text, encoding="utf-8")
    print(f"[ZH] PackageCatalog descriptions localized: {replaced}")
    return replaced


def main() -> None:
    root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    if not (root / "Cyanide.xcodeproj").exists() or not (root / "Cyanide").is_dir():
        fail("请在 Cyanide 仓库根目录运行，或把仓库路径作为第一个参数传入")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f".zh-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    files_to_backup = [
        "Cyanide/Base.lproj/Main.storyboard",
        "Cyanide/installer/MainTabBarController.m",
        "Cyanide/installer/HomeViewController.m",
        "Cyanide/installer/PackagesViewController.m",
        "Cyanide/installer/SourcesViewController.m",
        "Cyanide/installer/CategoryPackagesViewController.m",
        "Cyanide/installer/PackageDetailViewController.m",
        "Cyanide/installer/QueueReviewViewController.m",
        "Cyanide/installer/InstallProgressViewController.m",
        "Cyanide/SettingsViewController.m",
        "Cyanide/installer/PackageCatalog.m",
    ]
    for rel in files_to_backup:
        src = root / rel
        if src.exists():
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    total = 0

    total += patch_literals(root, "Cyanide/Base.lproj/Main.storyboard", {
        'title="Settings"': 'title="设置"',
        'title="Packages"': 'title="软件包"',
        'title="Log"': 'title="日志"',
    })

    total += patch_literals(root, "Cyanide/installer/MainTabBarController.m", {
        objc("Packages"): objc("软件包"),
        objc("Home"): objc("首页"),
        objc("Sources"): objc("软件源"),
        objc("Refreshing sources…"): objc("正在刷新软件源…"),
        objc("Sources up to date"): objc("软件源已是最新"),
    }, required=(objc("Packages"), objc("Sources")))

    total += patch_literals(root, "Cyanide/installer/HomeViewController.m", {
        objc("Home"): objc("首页"),
        objc("SpringBoard tweaks for stock iOS"): objc("为原生 iOS 提供 SpringBoard 插件"),
        objc("No jailbreak required · v%@"): objc("无需越狱 · v%@"),
        objc("Packages"): objc("软件包"),
        objc("Sources"): objc("软件源"),
        objc("What's New"): objc("更新内容"),
        objc("JavaScript tweak support by @MinePlayer16"): objc("支持由 @MinePlayer16 提供的 JavaScript 插件"),
        objc("Source repos with browsable tweak catalogs"): objc("软件源支持可浏览的插件目录"),
        objc("SnowBoard Lite and SpringBoard stability fixes"): objc("SnowBoard Lite 与 SpringBoard 稳定性修复"),
        objc("Get Started"): objc("开始使用"),
        objc("Open QuickLoader"): objc("打开 QuickLoader"),
        objc("Run a local .js tweak file"): objc("运行本地 .js 插件文件"),
        objc("Add a Source"): objc("添加软件源"),
        objc("Browse and install JS tweaks from repos"): objc("浏览并安装软件源中的 JS 插件"),
        objc("Community"): objc("社区"),
        objc("Signal Group"): objc("Signal 群组"),
        objc("Report a Bug"): objc("报告问题"),
    })

    total += patch_literals(root, "Cyanide/installer/PackagesViewController.m", {
        objc("Just now"): objc("刚刚"),
        objc("%ldm ago"): objc("%ld 分钟前"),
        objc("%ldh ago"): objc("%ld 小时前"),
        objc("Yesterday"): objc("昨天"),
        objc("%ldd ago"): objc("%ld 天前"),
        objc("MMM d"): objc("M月d日"),
        objc("Packages"): objc("软件包"),
        objc("Search all tweaks"): objc("搜索全部插件"),
        objc("Recently Added"): objc("最近添加"),
        objc("All Packages"): objc("全部软件包"),
        objc("Installed, unsupported here · %@ · %@"): objc("已安装，但当前环境不支持 · %@ · %@"),
        objc("Installed, unsupported here · %@"): objc("已安装，但当前环境不支持 · %@"),
        objc("Update available · %@ · %@"): objc("有可用更新 · %@ · %@"),
        objc("Update available · %@"): objc("有可用更新 · %@"),
        objc("Update available"): objc("有可用更新"),
        objc("INSTALLED"): objc("已安装"),
        objc("DISABLED"): objc("已禁用"),
        objc("UNSUPPORTED"): objc("不支持"),
        objc("UPDATE"): objc("更新"),
    })

    total += patch_literals(root, "Cyanide/installer/SourcesViewController.m", {
        objc("Sources"): objc("软件源"),
        objc("Source"): objc("软件源"),
        objc("Refresh this source before installing."): objc("安装前请先刷新此软件源。"),
        objc("INSTALLED"): objc("已安装"),
        objc("UNSUPPORTED"): objc("不支持"),
        objc("UPDATE"): objc("更新"),
        objc("Add Source"): objc("添加软件源"),
        objc("Source URL"): objc("软件源地址"),
        objc("Add"): objc("添加"),
        objc("Cancel"): objc("取消"),
        objc("Refresh All"): objc("刷新全部"),
        objc("Remove Source"): objc("删除软件源"),
        objc("Delete"): objc("删除"),
        objc("Categories"): objc("分类"),
        objc("Developer"): objc("开发者"),
        objc("JavaScript Tweak Docs"): objc("JavaScript 插件文档"),
        objc("QuickLoader"): objc("QuickLoader"),
    })

    total += patch_literals(root, "Cyanide/installer/CategoryPackagesViewController.m", {
        objc("Packages"): objc("软件包"),
        objc("Search"): objc("搜索"),
        objc("Run Hide Home Bar Alone"): objc("请单独运行 Hide Home Bar"),
        objc("Cannot Queue Install"): objc("无法加入安装队列"),
        objc("This package cannot be queued yet."): objc("当前还不能把这个软件包加入队列。"),
        objc("OK"): objc("好"),
        objc("These tweaks are visible for continuity only. Installing is disabled because they do not work yet; the unfinished app/source paths remain for anyone who wants to pick them up."): objc("这些插件仅为保留项目连续性而显示。由于功能尚未完成，当前禁止安装；未完成的应用/源码路径仍保留，方便后续继续开发。"),
        objc("Installed, unsupported here · %@ · %@"): objc("已安装，但当前环境不支持 · %@ · %@"),
        objc("Installed, unsupported here · %@"): objc("已安装，但当前环境不支持 · %@"),
        objc("DISABLED"): objc("已禁用"),
        objc("INSTALLED"): objc("已安装"),
        objc("ACTIVE"): objc("已启用"),
        objc("PENDING"): objc("待处理"),
    })

    total += patch_literals(root, "Cyanide/installer/PackageDetailViewController.m", {
        objc("Cancel"): objc("取消"),
        objc("OK"): objc("好"),
        objc("I Understand, Silence"): objc("我已了解，静音"),
        objc("Call Recording Disclosure"): objc("通话录音提示说明"),
        objc("Cancel Apply"): objc("取消应用"),
        objc("Cancel Remove"): objc("取消移除"),
        objc("Cancel Silence"): objc("取消静音"),
        objc("Cancel Restore"): objc("取消恢复"),
        objc("Cancel Hide"): objc("取消隐藏"),
        objc("Cancel Disable"): objc("取消禁用"),
        objc("Cancel Enable"): objc("取消启用"),
        objc("Apply/Remove"): objc("应用/移除"),
        objc("Silence/Restore"): objc("静音/恢复"),
        objc("Restore"): objc("恢复"),
        objc("Hide"): objc("隐藏"),
        objc("Disable/Enable"): objc("禁用/启用"),
        objc("Apply Pending"): objc("等待应用"),
        objc("Remove Pending"): objc("等待移除"),
        objc("Silence Pending"): objc("等待静音"),
        objc("Restore Pending"): objc("等待恢复"),
        objc("Hide Pending"): objc("等待隐藏"),
        objc("Hidden"): objc("已隐藏"),
        objc("Ready"): objc("就绪"),
        objc("Disable Pending"): objc("等待禁用"),
        objc("Enable Pending"): objc("等待启用"),
        objc("Manual Control"): objc("手动控制"),
        objc("Update Pending"): objc("等待更新"),
        objc("Install Pending"): objc("等待安装"),
        objc("Removal Pending"): objc("等待移除"),
        objc("Update Available"): objc("有可用更新"),
        objc("Installed"): objc("已安装"),
        objc("Available"): objc("可用"),
        objc("Activation Pending"): objc("等待启用"),
        objc("Deactivation Pending"): objc("等待停用"),
        objc("Inactive"): objc("未启用"),
        objc("Run Hide Home Bar Alone"): objc("请单独运行 Hide Home Bar"),
        objc("Cannot Queue Install"): objc("无法加入安装队列"),
        objc("This package cannot be queued yet."): objc("当前还不能把这个软件包加入队列。"),
        objc("State"): objc("状态"),
        objc("Apply Pairing Override"): objc("应用配对覆盖"),
        objc("Remove Pairing Override"): objc("移除配对覆盖"),
        objc("Description"): objc("描述"),
        objc("Version"): objc("版本"),
        objc("Author"): objc("作者"),
        objc("Category"): objc("分类"),
        objc("Current Settings"): objc("当前设置"),
        objc("Known Issues"): objc("已知问题"),
        objc("Warning"): objc("警告"),
        objc("Settings"): objc("设置"),
        objc("Customize"): objc("自定义"),
        objc("Activate"): objc("启用"),
        objc("Deactivate"): objc("停用"),
        objc("Install"): objc("安装"),
        objc("Remove"): objc("移除"),
        objc("Update"): objc("更新"),
    })

    total += patch_literals(root, "Cyanide/installer/QueueReviewViewController.m", {
        objc("Queue"): objc("队列"),
        objc("No pending changes\nQueue packages from the Packages tab"): objc("没有待处理的更改\n请从“软件包”页面把插件加入队列"),
        objc("Confirm"): objc("确认"),
        objc("Clear Queue"): objc("清空队列"),
        objc("Confirm 1 Change"): objc("确认 1 项更改"),
        objc("Confirm %ld Changes"): objc("确认 %ld 项更改"),
        objc("Hide Home Bar must run alone"): objc("Hide Home Bar 必须单独运行"),
        objc("It edits the system home-indicator asset and then needs a respring. Confirm only Hide Home Bar, respring, then queue your other tweaks."): objc("它会修改系统 Home 指示条资源，随后需要 Respring。请只确认 Hide Home Bar，完成 Respring 后再把其他插件加入队列。"),
        objc("Disable"): objc("禁用"),
        objc("Apply"): objc("应用"),
        objc("Silence"): objc("静音"),
        objc("Hide"): objc("隐藏"),
        objc("Install"): objc("安装"),
        objc("Activate"): objc("启用"),
        objc("Enable"): objc("启用"),
        objc("Remove"): objc("移除"),
        objc("Restore"): objc("恢复"),
        objc("Deactivate"): objc("停用"),
        objc("Already Active"): objc("已经启用"),
        objc("No longer pending"): objc("已不在待处理队列"),
        objc("This queue row was already applied or cleared."): objc("这一项已经执行或被清除。"),
    })

    total += patch_literals(root, "Cyanide/installer/InstallProgressViewController.m", {
        objc("Activity"): objc("执行进度"),
        objc("Running — stay here until complete."): objc("正在执行，请在完成前保持此页面。"),
        objc("Hide"): objc("隐藏"),
        objc("All tweaks applied in-session."): objc("所有插件已在当前会话中应用。"),
        objc("Failed — check the log above."): objc("执行失败，请检查上方日志。"),
        objc("Complete"): objc("完成"),
        objc("Failed"): objc("失败"),
        objc("Done"): objc("完成"),
    })

    total += patch_literals(root, "Cyanide/SettingsViewController.m", {
        objc("Tweak Infos"): objc("插件信息"),
        objc("Tweak Status"): objc("插件状态"),
        objc("Personalization Options"): objc("个性化选项"),
        objc("Description"): objc("描述"),
        objc("Version"): objc("版本"),
        objc("Enable Tweak"): objc("启用插件"),
        objc("Refresh rate"): objc("刷新频率"),
        objc("Refresh interval"): objc("刷新间隔"),
        objc("0.25-5.00 seconds. 0.50s is a good fast default; lower values use more CPU/battery."): objc("可设置 0.25～5.00 秒。想要更实时建议用 0.50 秒；数值越低，CPU 与耗电开销越高。"),
        objc("Transparent CPU / GPU / RAM performance HUD. The overlay follows the frontmost app's UI orientation, so landscape-only games no longer depend on SpringBoard's physical/device orientation reporting. Portrait placement stays below the safe area / Dynamic Island; landscape placement stays top-center. CPU keeps the last valid sample through brief sub-second tick gaps instead of flashing --. GPU safely shows -- when unavailable. Refresh interval supports 0.25-5.00 seconds; the HUD pauses while the screen is locked or asleep."): objc("透明的 CPU / GPU / 内存性能悬浮窗。横竖屏会跟随前台 App 的实际 UI 朝向，竖屏时自动放到安全区域下方以避开灵动岛，横屏时保持顶部居中，并且不使用文字阴影。亚秒级刷新时如果 CPU tick 短暂没有推进，会继续显示上一帧有效 CPU 数值，避免闪成 --。GPU 不可用时安全显示 --。刷新间隔支持 0.25～5.00 秒；锁屏或休眠时悬浮窗会暂停刷新。"),
        objc("Metrics"): objc("指标"),
        objc("Background"): objc("背景"),
        objc("Transparent"): objc("透明"),
        objc("Settings"): objc("设置"),
        objc("Tweaks"): objc("插件"),
        objc("Status Bar Time Format"): objc("状态栏时间格式"),
        objc("Apply"): objc("应用"),
        objc("SpringBoard"): objc("SpringBoard"),
        objc("Hide Icon Labels"): objc("隐藏图标名称"),
        objc("Home columns"): objc("主屏幕列数"),
        objc("rows"): objc("行数"),
        objc("Apply Home Screen Grid"): objc("应用主屏幕网格"),
        objc("Dock columns"): objc("Dock 列数"),
        objc("Apply Dock Columns"): objc("应用 Dock 列数"),
        objc("Enable Upside Down"): objc("启用上下倒置"),
        objc("Enable Floating Dock"): objc("启用悬浮 Dock"),
        objc("Enable Grid App Switcher (Broken animation)"): objc("启用网格多任务切换器（动画存在问题）"),
        objc("Enable UIKit Debug Overlay"): objc("启用 UIKit 调试浮层"),
        objc("Performance HUD"): objc("性能 HUD"),
        objc("RemoteCall"): objc("RemoteCall"),
        objc("Overwrite eligibility (one time setup)"): objc("覆盖资格状态（一次性设置）"),
        objc("Enable Spoof EU Region"): objc("启用欧盟地区伪装"),
        objc("Enables installing of EU/Japan Marketplace apps."): objc("用于尝试安装欧盟/日本 Marketplace 应用。"),
        objc("Generic Youtube Tweaks"): objc("通用 YouTube 插件"),
        objc("Tools"): objc("工具"),
        objc("Respring"): objc("Respring"),
        objc("Custom RemoteCall"): objc("自定义 RemoteCall"),
        objc("Target"): objc("目标进程"),
        objc("Symbol"): objc("符号"),
        objc("Timeout"): objc("超时"),
        objc("MIG filter bypass"): objc("绕过 MIG 过滤"),
        objc("Call"): objc("调用"),
        objc("Show CPU %"): objc("显示 CPU %"),
        objc("Show CPU / RAM labels"): objc("显示 CPU / RAM 标签"),
        objc("Show network speed"): objc("显示网络速度"),
        objc("Network speed only"): objc("仅显示网络速度"),
        objc("Celsius"): objc("摄氏度"),
    })

    localize_package_catalog(root)

    print(f"[ZH] finished, total literal replacements: {total}")
    print(f"[ZH] backup: {backup}")
    print("[ZH] 技术名、插件名、第三方 JS 插件自带文案会保留原文，避免破坏标识符和兼容逻辑。")


if __name__ == "__main__":
    main()
