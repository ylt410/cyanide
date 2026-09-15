#!/usr/bin/env python3
from pathlib import Path
import sys


def objc(s: str) -> str:
    return '@"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def patch_file(root: Path, rel: str, mapping: dict[str, str]) -> int:
    path = root / rel
    if not path.exists():
        print(f"[ZH-EXTRA] skip missing: {rel}")
        return 0

    text = path.read_text(encoding="utf-8")
    original = text
    total = 0

    for old, new in mapping.items():
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            total += count

    if text != original:
        path.write_text(text, encoding="utf-8")

    print(f"[ZH-EXTRA] {rel}: {total} replacements")
    return total


def main() -> None:
    root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    total = 0

    total += patch_file(root, "Cyanide/SettingsViewController.m", {
        # Settings root sections
        objc("What's New"): objc("更新内容"),
        objc("Quick Actions"): objc("快捷操作"),
        objc("Tweaks"): objc("插件"),
        objc("In Development"): objc("开发中"),
        objc("System"): objc("系统"),
        objc("About"): objc("关于"),

        # Common Settings UI
        objc("Settings"): objc("设置"),
        objc("Keep Alive"): objc("后台保活"),
        objc("Enabled"): objc("已启用"),
        objc("Disabled"): objc("已禁用"),
        objc("On"): objc("开"),
        objc("Off"): objc("关"),
        objc("Apply"): objc("应用"),
        objc("Target"): objc("目标"),
        objc("Status"): objc("状态"),
        objc("Current Settings"): objc("当前设置"),
        objc("Warning"): objc("警告"),
        objc("Description"): objc("描述"),
        objc("Version"): objc("版本"),
        objc("Enable Tweak"): objc("启用插件"),
        objc("Personalization Options"): objc("个性化选项"),
        objc("Tweak Infos"): objc("插件信息"),
        objc("Tweak Status"): objc("插件状态"),
        objc("Refresh rate"): objc("刷新频率"),
        objc("Refresh interval"): objc("刷新间隔"),
        objc("Background"): objc("背景"),
        objc("Transparent"): objc("透明"),
        objc("Metrics"): objc("指标"),
        objc("Tools"): objc("工具"),
        objc("Call"): objc("调用"),
        objc("Timeout"): objc("超时"),

        # Gravity Lite detail page
        objc("Include Dock"): objc("包含 Dock"),
        objc("Gravity strength"): objc("重力强度"),
        objc("Bounce"): objc("弹性"),
        objc("Friction"): objc("摩擦力"),
        objc("Resistance"): objc("阻力"),
        objc("Spin resistance"): objc("旋转阻力"),
        objc("Spin resist."): objc("旋转阻力"),
        objc("Explosion Pulse"): objc("爆炸脉冲"),
        objc("Restore Icon Layout"): objc("恢复图标布局"),
        objc("Strength"): objc("强度"),
        objc("Included"): objc("已包含"),
        objc("Home only"): objc("仅主屏幕"),
        objc("Dock icons"): objc("Dock 图标数"),
        objc("Home columns"): objc("主屏幕列数"),
        objc("Home rows"): objc("主屏幕行数"),
        objc("Hide icon labels"): objc("隐藏图标名称"),
        objc("After Apply, return to the Home Screen and shake the phone twice to start Gravity physics. Shake twice again to restore the icons. Cyanide automatically keeps its existing background runtime alive while the gesture is armed. Activator/Home-button hooks, drag gestures, and preference-daemon notifications are not included."):
            objc("应用后返回主屏幕，连续摇动手机两次即可启动 Gravity 物理效果；再次摇动两次会恢复图标。手势待命期间 Cyanide 会自动启动现有的后台保活。此版本不包含 Activator/Home 键钩子、拖拽手势和偏好守护进程通知。"),

        objc(
            "RemoteCall-only core port of Julio Verne's Gravity. Run applies UIDynamicAnimator gravity, collision, bounce, friction, optional dock physics, and accelerometer steering to SpringBoard icon snapshots. It can restore the icon layout or fire a manual explosion pulse while the SpringBoard session is active.\n\nNot included in this core port: Activator/Home-button hooks, drag gestures, automatic shake effects, and preference-daemon notifications."
        ): objc(
            "Julio Verne Gravity 的 RemoteCall 版本。运行后会为 SpringBoard 图标应用重力、碰撞、弹性、摩擦、可选 Dock 物理效果以及加速度计方向控制；当前 SpringBoard 会话存活时可恢复图标布局，也可手动触发爆炸脉冲。\n\n此核心版本不包含 Activator/Home 键钩子、拖拽手势及偏好守护进程通知。"
        ),

        # Duo Fold: independent top-level card + independent detail page
        objc("Enable Duo Fold"): objc("启用 Duo Fold"),
        objc("Motion-driven fold effect. Keep Alive is recommended."):
            objc("由手机运动姿态驱动折叠效果，建议开启后台保活。"),
        objc(
            "Uses iPhone motion as a virtual hinge to drive a fold-style SpringBoard effect. Enable it, apply pending tweaks, leave Cyanide in the background, then tilt the phone. Keep Alive is recommended for continuous motion updates."
        ): objc(
            "使用 iPhone 的运动传感器作为虚拟铰链，驱动 SpringBoard 折叠视觉效果。开启后应用待处理插件，将 Cyanide 切到后台，再倾斜手机即可。建议开启后台保活，以持续提供运动数据。"
        ),
    })

    total += patch_file(root, "Cyanide/installer/PackageCatalog.m", {
        objc("Motion-driven fold effect for SpringBoard"):
            objc("随手机姿态变化的 SpringBoard 折叠动效"),
        objc("Uses the iPhone motion sensors as a virtual hinge to drive a fold-style SpringBoard visual effect. Apply it here, return to the Home Screen, keep Cyanide in the background, then tilt the phone left or right. Cyanide automatically starts its existing background keep-alive while Duo Fold is active. No separate Settings switch or extra Run step is required."):
            objc("使用 iPhone 的运动传感器作为虚拟铰链，驱动 SpringBoard 的折叠视觉效果。直接在这里应用，然后返回主屏幕并让 Cyanide 保持在后台，左右倾斜手机即可看到效果。Duo Fold 启用期间会自动启动 Cyanide 现有的后台保活，不需要再去设置里找开关，也不需要额外执行 Run。"),
        objc("Experimental visual effect: it depends on a live Cyanide RemoteCall session and Core Motion updates. Locking the device, killing Cyanide from the App Switcher, or a SpringBoard restart stops the live effect."):
            objc("实验性视觉效果：依赖 Cyanide 持续的 RemoteCall 会话和运动传感器更新。锁屏、从多任务界面杀掉 Cyanide，或 SpringBoard 重启后，实时效果都会停止。"),
    })

    total += patch_file(root, "Cyanide/Base.lproj/Main.storyboard", {
        'title="Home"': 'title="首页"',
        'title="Settings"': 'title="设置"',
        'title="Packages"': 'title="软件包"',
        'title="Sources"': 'title="软件源"',
        'title="Log"': 'title="日志"',
    })

    total += patch_file(root, "Cyanide/installer/MainTabBarController.m", {
        objc("Home"): objc("首页"),
        objc("Packages"): objc("软件包"),
        objc("Sources"): objc("软件源"),
        objc("Settings"): objc("设置"),
        objc("Log"): objc("日志"),
    })

    print(f"[ZH-EXTRA] finished, total replacements: {total}")


if __name__ == "__main__":
    main()
