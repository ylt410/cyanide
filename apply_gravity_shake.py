#!/usr/bin/env python3
from pathlib import Path
import sys


def fail(msg: str) -> None:
    raise SystemExit(f"[GRAVITY-SHAKE] {msg}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        fail(f"anchor not found: {label}")
    if count > 1:
        fail(f"anchor not unique ({count} matches): {label}")
    return text.replace(old, new, 1)


def replace_region(text: str, start: str, end: str, replacement: str, label: str) -> str:
    a = text.find(start)
    if a < 0:
        fail(f"start marker not found: {label}")
    b = text.find(end, a)
    if b < 0:
        fail(f"end marker not found: {label}")
    if text.find(start, a + len(start)) >= 0 and text.find(start, a + len(start)) < b:
        fail(f"ambiguous start marker: {label}")
    return text[:a] + replacement.rstrip() + "\n\n" + text[b:]


def patch_gravity_core(root: Path):
    gravity_path = root / "Cyanide" / "tweaks" / "gravitylite.m"
    header_path = root / "Cyanide" / "tweaks" / "gravitylite.h"
    if not gravity_path.exists() or not header_path.exists():
        fail("missing Cyanide/tweaks/gravitylite.m or gravitylite.h")

    g = gravity_path.read_text(encoding="utf-8")
    h = header_path.read_text(encoding="utf-8")

    if "gravitylite_handoff_current_page_in_session" in g and "gl_restore_group" in g:
        return gravity_path, g, header_path, h

    h = replace_once(
        h,
        '#import <stdbool.h>\n',
        '#import <stdbool.h>\n#import <stdint.h>\n',
        'gravitylite.h stdint import')
    h = replace_once(
        h,
        'bool gravitylite_apply_in_session(GravityLiteConfig config);\nbool gravitylite_stop_in_session(void);\nbool gravitylite_explosion_in_session(double force);',
        'bool gravitylite_apply_in_session(GravityLiteConfig config);\n'
        '// Animated user-facing restore. The immediate variant is used internally for\n'
        '// stale-state cleanup and page handoff.\n'
        'bool gravitylite_stop_in_session(void);\n'
        'bool gravitylite_stop_immediate_in_session(void);\n'
        '// Returns the currently selected Home Screen icon-list object as a session-\n'
        '// scoped token. It is never dereferenced by the app.\n'
        'uint64_t gravitylite_current_page_token_in_session(void);\n'
        '// While Gravity Mode is active, restore only the old home-page group and move\n'
        '// physics to the newly selected page. A Dock group is kept alive.\n'
        'bool gravitylite_handoff_current_page_in_session(GravityLiteConfig config);\n'
        'bool gravitylite_explosion_in_session(double force);',
        'gravitylite.h page/restore API')

    g = replace_once(
        g,
        'static uint64_t gl_array_object(uint64_t array, uint64_t index)\n'
        '{\n'
        '    if (!r_is_objc_ptr(array)) return 0;\n'
        '    return r_msg2(array, "objectAtIndex:", index, 0, 0, 0);\n'
        '}\n',
        'static uint64_t gl_array_object(uint64_t array, uint64_t index)\n'
        '{\n'
        '    if (!r_is_objc_ptr(array)) return 0;\n'
        '    return r_msg2(array, "objectAtIndex:", index, 0, 0, 0);\n'
        '}\n\n'
        'static void gl_array_remove_at(uint64_t array, uint64_t index)\n'
        '{\n'
        '    if (!r_is_objc_ptr(array)) return;\n'
        '    r_msg2_main(array, "removeObjectAtIndex:", index, 0, 0, 0);\n'
        '}\n',
        'gravity array removal helper')

    # iOS 17/26 live-icon path: remember each icon's original overlay frame.
    g = replace_once(
        g,
        '    uint64_t liveItems = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveParents = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveFrames = gl_new_remote("NSMutableArray");\n'
        '    GL_CGRect overlayFrame = {0};',
        '    uint64_t liveItems = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveParents = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveFrames = gl_new_remote("NSMutableArray");\n'
        '    uint64_t targetFrames = gl_new_remote("NSMutableArray");\n'
        '    GL_CGRect overlayFrame = {0};',
        'live builder targetFrames allocation')
    g = replace_once(
        g,
        '        !r_is_objc_ptr(liveParents) ||\n'
        '        !r_is_objc_ptr(liveFrames) ||\n'
        '        !r_is_objc_ptr(overlay)) {',
        '        !r_is_objc_ptr(liveParents) ||\n'
        '        !r_is_objc_ptr(liveFrames) ||\n'
        '        !r_is_objc_ptr(targetFrames) ||\n'
        '        !r_is_objc_ptr(overlay)) {',
        'live builder targetFrames validation')
    g = replace_once(
        g,
        '        if (liveFrames) gl_release(liveFrames);\n'
        '        return false;\n'
        '    }\n\n'
        '    GL_CGRect overlayBounds = {0.0, 0.0, overlayFrame.w, overlayFrame.h};',
        '        if (liveFrames) gl_release(liveFrames);\n'
        '        if (targetFrames) gl_release(targetFrames);\n'
        '        return false;\n'
        '    }\n\n'
        '    GL_CGRect overlayBounds = {0.0, 0.0, overlayFrame.w, overlayFrame.h};',
        'live builder initial cleanup')
    g = replace_once(
        g,
        '        uint64_t frameValue = gl_value_with_rect(originalFrame);\n'
        '        if (!r_is_objc_ptr(frameValue)) continue;\n\n'
        '        gl_reset_transform(icon);',
        '        uint64_t frameValue = gl_value_with_rect(originalFrame);\n'
        '        uint64_t targetFrameValue = gl_value_with_rect(iconInOverlay);\n'
        '        if (!r_is_objc_ptr(frameValue) || !r_is_objc_ptr(targetFrameValue)) continue;\n\n'
        '        gl_reset_transform(icon);',
        'live builder target frame value')
    g = replace_once(
        g,
        '        gl_array_add(liveFrames, frameValue);\n'
        '        r_msg2_main(overlay, "addSubview:", icon, 0, 0, 0);\n'
        '        gl_set_rect(icon, "setFrame:", iconInOverlay);\n'
        '        gl_array_add(icons, icon);',
        '        gl_array_add(liveFrames, frameValue);\n'
        '        gl_array_add(targetFrames, targetFrameValue);\n'
        '        r_msg2_main(overlay, "addSubview:", icon, 0, 0, 0);\n'
        '        gl_set_rect(icon, "setFrame:", iconInOverlay);\n'
        '        gl_array_add(icons, icon);',
        'live builder save target frame')

    cleanup_pairs = [
        (
            'live no-icons cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n\n    uint64_t animator = gl_animator_for_reference_view(overlay);',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n\n    uint64_t animator = gl_animator_for_reference_view(overlay);'
        ),
        (
            'live animator cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n\n    uint64_t collision = gl_alloc_init_with_items("UICollisionBehavior", icons);',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n\n    uint64_t collision = gl_alloc_init_with_items("UICollisionBehavior", icons);'
        ),
        (
            'live group failure cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n\n    uint64_t isRunning = gl_safe_msg(animator, "isRunning", 0, 0, 0, 0);',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n\n    uint64_t isRunning = gl_safe_msg(animator, "isRunning", 0, 0, 0, 0);'
        ),
    ]
    for label, old, new in cleanup_pairs:
        g = replace_once(g, old, new, label)

    g = replace_once(
        g,
        '        gl_dict_set(group, "liveFrames", liveFrames);\n'
        '        gl_dict_set(group, "listView", listView);\n'
        '        gl_dict_set(group, "referenceView", overlay);',
        '        gl_dict_set(group, "liveFrames", liveFrames);\n'
        '        gl_dict_set(group, "targetFrames", targetFrames);\n'
        '        gl_dict_set(group, "gravity", gravity);\n'
        '        gl_dict_set(group, "listView", listView);\n'
        '        gl_dict_set(group, "referenceView", overlay);',
        'live group stores target/gravity')
    g = replace_once(
        g,
        '    gl_release(liveParents);\n'
        '    gl_release(liveFrames);\n'
        '    return true;\n'
        '}\n\n'
        'static bool gl_build_group(uint64_t groups,',
        '    gl_release(liveParents);\n'
        '    gl_release(liveFrames);\n'
        '    gl_release(targetFrames);\n'
        '    return true;\n'
        '}\n\n'
        'static bool gl_build_group(uint64_t groups,',
        'live builder success release')

    # Legacy/snapshot path. Save an unscaled grid target parallel to snapshots.
    g = replace_once(
        g,
        '    uint64_t snapshots = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveItems = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveParents = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveFrames = gl_new_remote("NSMutableArray");',
        '    uint64_t snapshots = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveItems = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveParents = gl_new_remote("NSMutableArray");\n'
        '    uint64_t liveFrames = gl_new_remote("NSMutableArray");\n'
        '    uint64_t targetFrames = gl_new_remote("NSMutableArray");',
        'snapshot builder targetFrames allocation')
    g = replace_once(
        g,
        '        !r_is_objc_ptr(liveItems) ||\n'
        '        !r_is_objc_ptr(liveParents) ||\n'
        '        !r_is_objc_ptr(liveFrames)) {',
        '        !r_is_objc_ptr(liveItems) ||\n'
        '        !r_is_objc_ptr(liveParents) ||\n'
        '        !r_is_objc_ptr(liveFrames) ||\n'
        '        !r_is_objc_ptr(targetFrames)) {',
        'snapshot builder targetFrames validation')
    g = replace_once(
        g,
        '        if (liveFrames) gl_release(liveFrames);\n'
        '        return false;\n'
        '    }\n\n'
        '    int added = 0;',
        '        if (liveFrames) gl_release(liveFrames);\n'
        '        if (targetFrames) gl_release(targetFrames);\n'
        '        return false;\n'
        '    }\n\n'
        '    int added = 0;',
        'snapshot builder initial cleanup')
    g = replace_once(
        g,
        '        if (!gl_convert_rect_to_view(icon, iconBounds, overlay, &iconInOverlay) ||\n'
        '            !gl_rect_valid(iconInOverlay)) continue;\n'
        '        if (!gl_rect_overlaps_bounds(iconInOverlay, overlayBounds)) continue;\n\n'
        '        bool widgetSizedItem = !isDock &&',
        '        if (!gl_convert_rect_to_view(icon, iconBounds, overlay, &iconInOverlay) ||\n'
        '            !gl_rect_valid(iconInOverlay)) continue;\n'
        '        if (!gl_rect_overlaps_bounds(iconInOverlay, overlayBounds)) continue;\n'
        '        GL_CGRect restoreTargetFrame = iconInOverlay;\n'
        '        uint64_t targetFrameValue = gl_value_with_rect(restoreTargetFrame);\n'
        '        if (!r_is_objc_ptr(targetFrameValue)) continue;\n\n'
        '        bool widgetSizedItem = !isDock &&',
        'snapshot restore target before scale')
    g = replace_once(
        g,
        '        gl_array_add(snapshots, physicsItem);\n'
        '        added++;',
        '        gl_array_add(snapshots, physicsItem);\n'
        '        gl_array_add(targetFrames, targetFrameValue);\n'
        '        added++;',
        'snapshot save target frame')

    cleanup_pairs = [
        (
            'snapshot no-items cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n    gl_set_double(listView, "setAlpha:", 0.0);',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n    gl_set_double(listView, "setAlpha:", 0.0);'
        ),
        (
            'snapshot animator cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n\n    uint64_t collision = gl_alloc_init_with_items("UICollisionBehavior", snapshots);',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n\n    uint64_t collision = gl_alloc_init_with_items("UICollisionBehavior", snapshots);'
        ),
        (
            'snapshot group failure cleanup',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        return false;\n    }\n\n\n    printf("[GRAVITY] Captured %s snapshots:',
            '        gl_release(liveParents);\n        gl_release(liveFrames);\n        gl_release(targetFrames);\n        return false;\n    }\n\n\n    printf("[GRAVITY] Captured %s snapshots:'
        ),
    ]
    for label, old, new in cleanup_pairs:
        g = replace_once(g, old, new, label)

    g = replace_once(
        g,
        '        gl_dict_set(group, "liveFrames", liveFrames);\n'
        '        gl_dict_set(group, "listView", listView);\n'
        '        gl_dict_set(group, "overlay", overlay);',
        '        gl_dict_set(group, "liveFrames", liveFrames);\n'
        '        gl_dict_set(group, "targetFrames", targetFrames);\n'
        '        gl_dict_set(group, "gravity", gravity);\n'
        '        gl_dict_set(group, "listView", listView);\n'
        '        gl_dict_set(group, "overlay", overlay);',
        'snapshot group stores target/gravity')
    g = replace_once(
        g,
        '    gl_release(liveParents);\n'
        '    gl_release(liveFrames);\n'
        '    return true;\n'
        '}\n\n'
        'bool gravitylite_stop_in_session(void)',
        '    gl_release(liveParents);\n'
        '    gl_release(liveFrames);\n'
        '    gl_release(targetFrames);\n'
        '    return true;\n'
        '}\n\n'
        'bool gravitylite_stop_in_session(void)',
        'snapshot builder success release')

    restore_block = r'''static void gl_rebuild_gravity_ptr_cache(uint64_t groups)
{
    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);
    memset(s_gravity_ptrs, 0, sizeof(s_gravity_ptrs));
    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t group = gl_array_object(groups, i);
        uint64_t gravity = gl_dict_get(group, "gravity");
        if (!r_is_objc_ptr(gravity)) continue;
        int n = __atomic_load_n(&s_gravity_ptr_count, __ATOMIC_RELAXED);
        if (n >= 8) break;
        s_gravity_ptrs[n] = gravity;
        __atomic_store_n(&s_gravity_ptr_count, n + 1, __ATOMIC_SEQ_CST);
    }
}

static int gl_restore_group(uint64_t group, bool animated)
{
    if (!r_is_objc_ptr(group)) return 0;

    uint64_t animator = gl_dict_get(group, "animator");
    uint64_t icons = gl_dict_get(group, "icons");
    uint64_t snapshots = gl_dict_get(group, "snapshots");
    uint64_t targetFrames = gl_dict_get(group, "targetFrames");
    uint64_t liveItems = gl_dict_get(group, "liveItems");
    uint64_t liveParents = gl_dict_get(group, "liveParents");
    uint64_t liveFrames = gl_dict_get(group, "liveFrames");
    uint64_t originalIcons = gl_dict_get(group, "originalIcons");
    uint64_t sources = gl_dict_get(group, "sources");
    uint64_t listView = gl_dict_get(group, "listView");
    uint64_t overlay = gl_dict_get(group, "overlay");

    if (r_is_objc_ptr(animator)) {
        r_msg2_main(animator, "removeAllBehaviors", 0, 0, 0, 0);
    }

    uint64_t resetItems = r_is_objc_ptr(icons) ? icons : snapshots;
    uint64_t itemCount = gl_array_count(resetItems);
    if (itemCount > 256) itemCount = 256;
    uint64_t targetCount = gl_array_count(targetFrames);
    if (targetCount < itemCount) itemCount = targetCount;

    int restoredVisuals = 0;
    bool canAnimate = animated && itemCount > 0 && r_is_objc_ptr(targetFrames);
    if (canAnimate) {
        uint64_t UIView = r_class("UIView");
        if (r_is_objc_ptr(UIView)) {
            r_msg2_main(UIView, "beginAnimations:context:", 0, 0, 0, 0);
            gl_set_double(UIView, "setAnimationDuration:", 0.48);
            gl_set_integer(UIView, "setAnimationCurve:", 0);
            gl_set_bool(UIView, "setAnimationBeginsFromCurrentState:", true);
            for (uint64_t j = 0; j < itemCount; j++) {
                uint64_t item = gl_array_object(resetItems, j);
                uint64_t targetValue = gl_array_object(targetFrames, j);
                GL_CGRect target;
                if (!r_is_objc_ptr(item) ||
                    !gl_rect_from_value(targetValue, &target) ||
                    !gl_rect_valid(target)) {
                    continue;
                }
                gl_reset_transform(item);
                gl_set_rect(item, "setFrame:", target);
                restoredVisuals++;
            }
            r_msg2_main(UIView, "commitAnimations", 0, 0, 0, 0);
            usleep(540000);
        } else {
            canAnimate = false;
        }
    }

    if (!canAnimate) {
        uint64_t n = gl_array_count(resetItems);
        if (n > 256) n = 256;
        for (uint64_t j = 0; j < n; j++) {
            uint64_t item = gl_array_object(resetItems, j);
            if (!r_is_objc_ptr(item)) continue;
            gl_reset_transform(item);
            restoredVisuals++;
        }
    }

    restoredVisuals += gl_restore_live_items(liveItems, liveParents, liveFrames);
    restoredVisuals += gl_unhide_icon_array(originalIcons);
    gl_set_array_views_alpha(sources, 1.0);
    if (r_is_objc_ptr(listView)) {
        gl_set_double(listView, "setAlpha:", 1.0);
        gl_layout_list_view(listView);
    }
    if (r_is_objc_ptr(overlay)) {
        r_msg2_main(overlay, "removeFromSuperview", 0, 0, 0, 0);
    }
    return restoredVisuals;
}

static bool gravitylite_stop_internal(bool animated)
{
    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);
    memset(s_gravity_ptrs, 0, sizeof(s_gravity_ptrs));

    uint64_t ctrl = gl_icon_controller();
    if (!r_is_objc_ptr(ctrl)) {
        printf("[GRAVITY] stop: SBIconController missing\n");
        return false;
    }

    uint64_t state = gl_get_state(ctrl);
    if (!r_is_objc_ptr(state)) {
        int orphans = gl_cleanup_gravity_overlays_in_app_windows();
        if (orphans > 0)
            printf("[GRAVITY] stop: removed %d orphaned overlay(s).\n", orphans);
        return true;
    }

    uint64_t groups = gl_dict_get(state, "groups");
    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    int restored = 0;
    for (uint64_t i = 0; i < count; i++) {
        restored += gl_restore_group(gl_array_object(groups, i), animated);
    }
    gl_set_state(ctrl, 0);

    int orphans = gl_cleanup_gravity_overlays_in_app_windows();
    if (orphans > 0) {
        printf("[GRAVITY] Cleaned up %d orphaned overlay(s) and restored %d visual item(s).\n",
               orphans, restored);
    } else {
        printf("[GRAVITY] Restored %d visual item(s)%s.\n",
               restored, animated ? " with animation" : "");
    }
    return true;
}

bool gravitylite_stop_immediate_in_session(void)
{
    uint32_t oldSettle = r_settle_us(0);
    bool ok = gravitylite_stop_internal(false);
    r_settle_us(oldSettle);
    return ok;
}

bool gravitylite_stop_in_session(void)
{
    uint32_t oldSettle = r_settle_us(0);
    bool ok = gravitylite_stop_internal(true);
    r_settle_us(oldSettle);
    return ok;
}'''
    g = replace_region(
        g,
        'bool gravitylite_stop_in_session(void)\n{',
        'bool gravitylite_apply_in_session(GravityLiteConfig config)',
        restore_block,
        'gravity animated restore engine')

    g = replace_once(
        g,
        '    (void)gravitylite_stop_in_session();\n'
        '    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);',
        '    (void)gravitylite_stop_immediate_in_session();\n'
        '    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);',
        'gravity apply uses immediate stale cleanup')

    handoff_block = r'''uint64_t gravitylite_current_page_token_in_session(void)
{
    uint32_t oldSettle = r_settle_us(0);
    uint64_t token = 0;
    uint64_t ctrl = gl_icon_controller();
    if (r_is_objc_ptr(ctrl)) {
        uint64_t mgr = gl_icon_manager(ctrl);
        uint64_t iconViewCls = r_class("SBIconView");
        int iosMajor = gl_remote_ios_major();
        bool useLiveIconPath = (iosMajor >= 26 || iosMajor == 17);
        uint64_t listView = useLiveIconPath
            ? gl_current_root_list_view_ios26_legacy(ctrl)
            : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
        if (!r_is_objc_ptr(listView)) listView = gl_current_root_list_view(ctrl, mgr);
        if (r_is_objc_ptr(listView)) token = listView;
    }
    r_settle_us(oldSettle);
    return token;
}

bool gravitylite_handoff_current_page_in_session(GravityLiteConfig config)
{
    uint32_t oldSettle = r_settle_us(0);
    bool ok = false;

    uint64_t ctrl = gl_icon_controller();
    uint64_t state = r_is_objc_ptr(ctrl) ? gl_get_state(ctrl) : 0;
    if (!r_is_objc_ptr(ctrl) || !r_is_objc_ptr(state)) goto done;

    uint64_t groups = gl_dict_get(state, "groups");
    if (!r_is_objc_ptr(groups)) goto done;

    uint64_t mgr = gl_icon_manager(ctrl);
    uint64_t iconViewCls = r_class("SBIconView");
    if (!r_is_objc_ptr(iconViewCls)) goto done;

    int iosMajor = gl_remote_ios_major();
    bool useLiveIconPath = (iosMajor >= 26 || iosMajor == 17);
    uint64_t dockListView = gl_dock_list_view_for_path(ctrl, mgr, useLiveIconPath);
    uint64_t desiredPage = useLiveIconPath
        ? gl_current_root_list_view_ios26_legacy(ctrl)
        : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
    if (!r_is_objc_ptr(desiredPage)) desiredPage = gl_current_root_list_view(ctrl, mgr);
    if (!r_is_objc_ptr(desiredPage)) goto done;

    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t group = gl_array_object(groups, i);
        uint64_t listView = gl_dict_get(group, "listView");
        if (r_is_objc_ptr(listView) && listView == desiredPage) {
            gl_rebuild_gravity_ptr_cache(groups);
            ok = true;
            goto done;
        }
    }

    for (int64_t i = (int64_t)count - 1; i >= 0; i--) {
        uint64_t group = gl_array_object(groups, (uint64_t)i);
        uint64_t listView = gl_dict_get(group, "listView");
        bool isDock = config.includeDock && r_is_objc_ptr(dockListView) && listView == dockListView;
        if (isDock) continue;
        (void)gl_restore_group(group, false);
        gl_array_remove_at(groups, (uint64_t)i);
    }

    gl_rebuild_gravity_ptr_cache(groups);
    usleep(180000);

    desiredPage = useLiveIconPath
        ? gl_current_root_list_view_ios26_legacy(ctrl)
        : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
    if (!r_is_objc_ptr(desiredPage)) desiredPage = gl_current_root_list_view(ctrl, mgr);
    if (!r_is_objc_ptr(desiredPage)) goto done;

    ok = gl_build_group(groups, desiredPage, iconViewCls, config, false, useLiveIconPath);
    if (ok) {
        printf("[GRAVITY] Physics handed to current home page 0x%llx; Dock preserved=%d.\n",
               (unsigned long long)desiredPage,
               (config.includeDock && r_is_objc_ptr(dockListView)) ? 1 : 0);
    }

done:
    r_settle_us(oldSettle);
    return ok;
}

'''
    g = replace_once(
        g,
        'bool gravitylite_explosion_in_session(double force)\n{',
        handoff_block + 'bool gravitylite_explosion_in_session(double force)\n{',
        'gravity page handoff API')

    return gravity_path, g, header_path, h

def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    path = root / "Cyanide" / "SettingsViewController.m"
    if not path.exists():
        fail(f"missing {path}")
    text = path.read_text(encoding="utf-8")

    # Idempotence: do not stack the patch if the workflow is re-run on an already patched tree.
    if "settings_gravity_maybe_handoff_page_async" in text and "Double-shake detector armed" in text and "gravitylite_handoff_current_page_in_session" in (root / "Cyanide" / "tweaks" / "gravitylite.m").read_text(encoding="utf-8"):
        print("[GRAVITY-SHAKE] v3 already applied")
        return

    # Older v1/v2 Settings-only patch must not be stacked onto itself.
    if "settings_gravity_apply_quick" in text and "Double-shake detector armed" in text:
        fail("an older GravityShake patch is already present in SettingsViewController.m; build from the clean Cyanide source so v3 can patch Settings + gravitylite together")

    old_import = '#import "tweaks/gravitylite.h"\n'
    new_import = '#import "tweaks/gravitylite.h"\n#import "tweaks/remote_objc.h"\n'
    if '#import "tweaks/remote_objc.h"' not in text:
        text = replace_once(text, old_import, new_import, "remote_objc import")

    old_globals = '''static volatile int g_gravity_motion_stop_requested = 1;
static volatile uint64_t g_gravity_motion_generation = 0;
static CMMotionManager *g_gravity_motion_manager = nil;'''
    new_globals = '''static volatile int g_gravity_motion_stop_requested = 1;
static volatile uint64_t g_gravity_motion_generation = 0;
static CMMotionManager *g_gravity_motion_manager = nil;
// Gravity Lite shake-toggle state. The sensor feed lives in Cyanide; the
// actual UIDynamics objects live in SpringBoard through the existing
// RemoteCall session.
static volatile int g_gravitylite_physics_active = 0;
static volatile int g_gravity_shake_toggle_running = 0;
static volatile uint64_t g_gravity_shake_first_pulse_us = 0;
static volatile uint64_t g_gravity_shake_last_pulse_us = 0;
static volatile uint64_t g_gravity_shake_cooldown_until_us = 0;
static volatile int g_gravity_shake_waiting_for_release = 0;
// While physics is active, poll the current SpringBoard icon page at a much
// lower cadence than CoreMotion. Page handoff restores only the old home-page
// group, keeps the Dock group alive, then captures the newly visible page.
static volatile uint64_t g_gravity_page_probe_after_us = 0;
static volatile uint64_t g_gravity_page_token = 0;
static volatile int g_gravity_page_handoff_running = 0;'''
    text = replace_once(text, old_globals, new_globals, "gravity global state")

    old_decl = '''static void settings_mark_tweak_applied(NSString *key, BOOL applied);
static void settings_notify_package_queue_changed_async(void);'''
    new_decl = '''static void settings_mark_tweak_applied(NSString *key, BOOL applied);
static void settings_notify_package_queue_changed_async(void);
static GravityLiteConfig settings_gravitylite_config_from_defaults(NSUserDefaults *d);
static bool settings_arm_gravitylite_for_background_start_locked(NSUserDefaults *d,
                                                                 const char *reason);'''
    text = replace_once(text, old_decl, new_decl, "gravity forward declarations")

    motion_start = "static BOOL settings_gravity_motion_can_remote_call(uint64_t generation,"
    motion_end = "typedef void (*SettingsTweakRequestStopFunc)(void);"
    motion_block = r'''static BOOL settings_gravity_motion_can_remote_call(uint64_t generation,
                                                    CMMotionManager *manager)
{
    return manager &&
           manager == g_gravity_motion_manager &&
           generation == g_gravity_motion_generation &&
           g_gravity_motion_stop_requested == 0 &&
           g_springboard_rc_ready != 0 &&
           !settings_screen_locked_cached() &&
           settings_screen_awake_cached() &&
           !settings_cleanup_in_progress();
}

static uint64_t settings_gravity_sensor_now_us(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return ((uint64_t)ts.tv_sec * 1000000ULL) + ((uint64_t)ts.tv_nsec / 1000ULL);
}

static void settings_gravity_reset_shake_detector(void)
{
    __sync_lock_test_and_set(&g_gravity_shake_first_pulse_us, 0);
    __sync_lock_test_and_set(&g_gravity_shake_last_pulse_us, 0);
    __sync_lock_test_and_set(&g_gravity_shake_cooldown_until_us, 0);
    __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 0);
}

static void settings_gravity_reset_page_tracking(void)
{
    __sync_lock_test_and_set(&g_gravity_page_probe_after_us, 0);
    __sync_lock_test_and_set(&g_gravity_page_token, 0);
    __sync_lock_test_and_set(&g_gravity_page_handoff_running, 0);
}

// Gravity's generic RemoteCall path deliberately inserts a 50 ms settle before
// many Objective-C messages. A capture/restore transaction contains dozens of
// such messages. Gravity already disables that delay inside its per-icon hot
// loops; these wrappers extend the same optimization across the whole gesture.
static bool settings_gravity_apply_quick(GravityLiteConfig config)
{
    uint64_t started = settings_gravity_sensor_now_us();
    uint32_t oldSettle = r_settle_us(0);
    bool ok = gravitylite_apply_in_session(config);
    r_settle_us(oldSettle);
    uint64_t ended = settings_gravity_sensor_now_us();
    if (started && ended >= started) {
        printf("[GRAVITY] fast apply elapsed=%.3fs ok=%d\n",
               (double)(ended - started) / 1000000.0, ok);
    }
    return ok;
}

static bool settings_gravity_restore_quick(void)
{
    uint64_t started = settings_gravity_sensor_now_us();
    uint32_t oldSettle = r_settle_us(0);
    bool ok = gravitylite_stop_in_session();
    r_settle_us(oldSettle);
    uint64_t ended = settings_gravity_sensor_now_us();
    if (started && ended >= started) {
        printf("[GRAVITY] fast restore elapsed=%.3fs ok=%d\n",
               (double)(ended - started) / 1000000.0, ok);
    }
    return ok;
}

static void settings_gravity_maybe_handoff_page_async(void)
{
    if (g_gravitylite_physics_active == 0 ||
        g_gravity_motion_stop_requested != 0 ||
        !g_springboard_rc_ready ||
        settings_cleanup_in_progress()) {
        return;
    }

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0 || now < g_gravity_page_probe_after_us) return;
    __sync_lock_test_and_set(&g_gravity_page_probe_after_us, now + 250000ULL);
    if (__sync_lock_test_and_set(&g_gravity_page_handoff_running, 1)) return;

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @try {
            NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
            @synchronized (settings_rc_lock()) {
                if (g_gravitylite_physics_active == 0 ||
                    g_gravity_motion_stop_requested != 0 ||
                    !g_springboard_rc_ready ||
                    settings_cleanup_in_progress() ||
                    ![d boolForKey:kSettingsGravityLiteEnabled]) {
                    return;
                }

                uint32_t oldSettle = r_settle_us(0);
                uint64_t token = gravitylite_current_page_token_in_session();
                uint64_t active = g_gravity_page_token;
                if (token != 0 && active == 0) {
                    // A previous handoff may have restored the outgoing page
                    // but hit the new page while SpringBoard was still laying
                    // it out. Ask the core to (re)attach the current page; it
                    // is a no-op if that page already owns a physics group.
                    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
                    bool ok = gravitylite_handoff_current_page_in_session(config);
                    uint64_t current = ok ? gravitylite_current_page_token_in_session() : 0;
                    if (ok && current != 0) {
                        __sync_lock_test_and_set(&g_gravity_page_token, current);
                    }
                } else if (token != 0 && active != 0 && token != active) {
                    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
                    printf("[GRAVITY] home page changed 0x%llx -> 0x%llx; handing physics to new page\n",
                           (unsigned long long)active,
                           (unsigned long long)token);
                    bool ok = gravitylite_handoff_current_page_in_session(config);
                    uint64_t current = ok ? gravitylite_current_page_token_in_session() : 0;
                    if (ok && current != 0) {
                        __sync_lock_test_and_set(&g_gravity_page_token, current);
                        printf("[GRAVITY] page handoff complete token=0x%llx\n",
                               (unsigned long long)current);
                    } else {
                        // Leave physics mode armed. A later probe can retry after
                        // SpringBoard finishes its paging animation/layout pass.
                        __sync_lock_test_and_set(&g_gravity_page_token, 0);
                        printf("[GRAVITY] page handoff deferred; will retry\n");
                    }
                }
                r_settle_us(oldSettle);
            }
        } @finally {
            __sync_lock_release(&g_gravity_page_handoff_running);
        }
    });
}

static void settings_gravity_toggle_physics_from_shake_async(void)
{
    NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
    if (![d boolForKey:kSettingsGravityLiteEnabled]) return;
    if ([UIApplication sharedApplication].applicationState != UIApplicationStateBackground) return;
    if (settings_cleanup_in_progress() || !g_springboard_rc_ready) return;
    if (__sync_lock_test_and_set(&g_gravity_shake_toggle_running, 1)) return;

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        BOOL turningOn = (g_gravitylite_physics_active == 0);
        bool ok = false;
        @try {
            @synchronized (settings_rc_lock()) {
                if (settings_cleanup_in_progress() ||
                    !g_springboard_rc_ready ||
                    ![d boolForKey:kSettingsGravityLiteEnabled] ||
                    !settings_screen_awake_cached() ||
                    settings_screen_locked_cached()) {
                    return;
                }

                if (turningOn) {
                    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
                    // apply_in_session begins by cleaning any stale Gravity state,
                    // so a missed prior restore cannot accumulate behaviors.
                    ok = settings_gravity_apply_quick(config);
                    if (ok) {
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 1);
                        __sync_lock_test_and_set(&g_gravity_page_token,
                                                 gravitylite_current_page_token_in_session());
                        __sync_lock_test_and_set(&g_gravity_page_probe_after_us,
                                                 settings_gravity_sensor_now_us() + 250000ULL);
                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);
                    }
                } else {
                    ok = settings_gravity_restore_quick();
                    if (ok) {
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                        settings_gravity_reset_page_tracking();
                        // "Applied" now means the shake feature is armed. Keep
                        // the package installed even while icons are restored.
                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);
                    }
                }
            }

            if (ok) {
                log_user(turningOn
                    ? "[GRAVITY] Double shake: physics ON. Shake twice again to restore.\n"
                    : "[GRAVITY] Double shake: icons restored. Shake twice again for physics.\n");
                printf("[GRAVITY] double-shake toggle -> %s\n", turningOn ? "ON" : "OFF");
                settings_notify_package_queue_changed_async();
            } else {
                printf("[GRAVITY] double-shake toggle failed target=%s\n",
                       turningOn ? "ON" : "OFF");
            }
        } @finally {
            __sync_lock_release(&g_gravity_shake_toggle_running);
        }
    });
}

static void settings_gravity_process_shake_sample(double x,
                                                  double y,
                                                  double z,
                                                  BOOL gravityRemoved)
{
    if ([UIApplication sharedApplication].applicationState != UIApplicationStateBackground) return;
    if (g_gravity_motion_stop_requested != 0 || !g_springboard_rc_ready) return;

    double a = sqrt(x * x + y * y + z * z);
    // CMDeviceMotion.userAcceleration has gravity removed and rests near 0g.
    // Raw accelerometer data rests near 1g, so use a higher threshold there.
    const double trigger = gravityRemoved ? 1.35 : 2.25;
    const double release = gravityRemoved ? 0.90 : 1.55;
    const uint64_t minPulseGapUS = 260000ULL;
    const uint64_t doubleShakeWindowUS = 1800000ULL;
    const uint64_t postToggleCooldownUS = 1200000ULL;

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0) return;
    if (now < g_gravity_shake_cooldown_until_us) return;

    if (a <= release) {
        __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 0);
        return;
    }
    if (a < trigger || g_gravity_shake_waiting_for_release != 0) return;

    uint64_t last = g_gravity_shake_last_pulse_us;
    if (last != 0 && now > last && (now - last) < minPulseGapUS) return;

    __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 1);
    __sync_lock_test_and_set(&g_gravity_shake_last_pulse_us, now);

    uint64_t first = g_gravity_shake_first_pulse_us;
    if (first == 0 || now <= first || (now - first) > doubleShakeWindowUS) {
        __sync_lock_test_and_set(&g_gravity_shake_first_pulse_us, now);
        printf("[GRAVITY] hard-shake 1/2 detected acceleration=%.2fg\n", a);
        return;
    }

    __sync_lock_test_and_set(&g_gravity_shake_first_pulse_us, 0);
    __sync_lock_test_and_set(&g_gravity_shake_cooldown_until_us,
                             now + postToggleCooldownUS);
    printf("[GRAVITY] hard-shake 2/2 detected acceleration=%.2fg\n", a);
    settings_gravity_toggle_physics_from_shake_async();
}

static void settings_start_gravity_motion(double magnitude, double explosionForce)
{
    (void)explosionForce;
    if (g_gravity_motion_manager) {
        [g_gravity_motion_manager stopDeviceMotionUpdates];
        [g_gravity_motion_manager stopAccelerometerUpdates];
        g_gravity_motion_manager = nil;
    }
    settings_gravity_reset_shake_detector();

    CMMotionManager *mm = [[CMMotionManager alloc] init];
    g_gravity_motion_manager = mm;
    uint64_t generation = __sync_add_and_fetch(&g_gravity_motion_generation, 1);
    __sync_lock_test_and_set(&g_gravity_motion_stop_requested, 0);
    NSOperationQueue *q = [[NSOperationQueue alloc] init];
    q.maxConcurrentOperationCount = 1;

    if (mm.deviceMotionAvailable) {
        mm.deviceMotionUpdateInterval = 0.04;
        [mm startDeviceMotionUpdatesToQueue:q withHandler:^(CMDeviceMotion *motion, NSError *err) {
            if (!motion || err || !settings_gravity_motion_can_remote_call(generation, mm)) return;

            settings_gravity_process_shake_sample(motion.userAcceleration.x,
                                                  motion.userAcceleration.y,
                                                  motion.userAcceleration.z,
                                                  YES);

            // Do not spend RemoteCalls on tilt steering while physics is off.
            if (g_gravitylite_physics_active == 0) return;
            settings_gravity_maybe_handoff_page_async();

            double tilt = hypot(motion.gravity.x, motion.gravity.y);
            double angle = (tilt < 0.14) ? M_PI_2 : atan2(-motion.gravity.y, motion.gravity.x);
            double effectiveMagnitude = magnitude * ((tilt < 0.14)
                                                     ? 0.65
                                                     : (0.90 + fmin(tilt, 1.0) * 0.60));
            @synchronized (settings_rc_lock()) {
                if (!settings_gravity_motion_can_remote_call(generation, mm) ||
                    g_gravitylite_physics_active == 0) return;
                gravitylite_update_gravity_angle_in_session(angle, effectiveMagnitude);
            }
        }];
    } else {
        mm.accelerometerUpdateInterval = 0.04;
        [mm startAccelerometerUpdatesToQueue:q withHandler:^(CMAccelerometerData *data, NSError *err) {
            if (!data || err || !settings_gravity_motion_can_remote_call(generation, mm)) return;

            settings_gravity_process_shake_sample(data.acceleration.x,
                                                  data.acceleration.y,
                                                  data.acceleration.z,
                                                  NO);

            if (g_gravitylite_physics_active == 0) return;
            settings_gravity_maybe_handoff_page_async();

            double tilt = hypot(data.acceleration.x, data.acceleration.y);
            double angle = (tilt < 0.14) ? M_PI_2 : atan2(-data.acceleration.y, data.acceleration.x);
            double effectiveMagnitude = magnitude * ((tilt < 0.14)
                                                     ? 0.65
                                                     : (0.90 + fmin(tilt, 1.2) * 0.50));
            @synchronized (settings_rc_lock()) {
                if (!settings_gravity_motion_can_remote_call(generation, mm) ||
                    g_gravitylite_physics_active == 0) return;
                gravitylite_update_gravity_angle_in_session(angle, effectiveMagnitude);
            }
        }];
    }
    printf("[GRAVITY] Double-shake detector armed — two hard shakes toggle physics; tilt steering active only while physics is ON (magnitude=%.1fx)\n",
           magnitude);
}

static void settings_stop_gravity_motion(void)
{
    __sync_lock_test_and_set(&g_gravity_motion_stop_requested, 1);
    __sync_add_and_fetch(&g_gravity_motion_generation, 1);
    settings_gravity_reset_shake_detector();
    settings_gravity_reset_page_tracking();
    CMMotionManager *mm = g_gravity_motion_manager;
    if (!mm) return;
    g_gravity_motion_manager = nil;
    [mm stopDeviceMotionUpdates];
    [mm stopAccelerometerUpdates];
    printf("[GRAVITY] Accelerometer stopped.\n");
}'''
    text = replace_region(text, motion_start, motion_end, motion_block, "gravity sensor/toggle engine")

    old_request = '''static void settings_request_gravitylite_stop(void)
{
    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
    settings_stop_gravity_motion();
}'''
    new_request = '''static void settings_request_gravitylite_stop(void)
{
    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
    __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
    settings_stop_gravity_motion();
}'''
    text = replace_once(text, old_request, new_request, "gravity stop request")

    apply_start = "static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)"
    apply_end = "static void settings_restart_gravity_motion_if_active(const char *reason)"
    new_apply = r'''static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)
{
    // Gravity Lite is now an armed shake gesture instead of an immediate
    // physics apply. The first double-shake starts physics; the next restores.
    return settings_arm_gravitylite_for_background_start_locked(d, "apply");
}'''
    text = replace_region(text, apply_start, apply_end, new_apply, "gravity apply semantics")

    arm_start = "static bool settings_arm_gravitylite_for_background_start_locked(NSUserDefaults *d,\n                                                                 const char *reason)\n{"
    arm_end = "static BOOL settings_gravitylite_start_window_ready(const char *reason)"
    new_arm = r'''static bool settings_arm_gravitylite_for_background_start_locked(NSUserDefaults *d,
                                                                 const char *reason)
{
    if (![d boolForKey:kSettingsGravityLiteEnabled]) return false;

    // Enabling/configuring Gravity should leave the desktop in its normal
    // layout. Physics starts only after a deliberate double shake.
    bool restored = settings_gravity_restore_quick();
    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
    __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
    settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);

    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
    settings_start_gravity_motion(config.magnitude, config.explosionForce);
    printf("[SETTINGS] Gravity Lite armed for double-shake toggle%s%s restore=%d\n",
           reason ? ": " : "", reason ?: "", restored);
    return true;
}'''
    text = replace_region(text, arm_start, arm_end, new_arm, "gravity background arm")

    run_start = '''                    if (runGravityLite) {
                        settings_progress(&step, total, "Starting Gravity Lite icon physics");'''
    run_end = '''                    } else if (!gravityLiteEnabled) {'''
    run_replacement = r'''                    if (runGravityLite) {
                        settings_progress(&step, total, "Arming Gravity Lite double-shake physics");
                        log_user("[GRAVITY] Arming double-shake physics trigger...\n");
                        __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                        settings_stop_gravity_motion();
                        settings_gravity_restore_quick();
                        GravityLiteConfig glConfig = settings_gravitylite_config_from_defaults(d);
                        settings_start_gravity_motion(glConfig.magnitude,
                                                      glConfig.explosionForce);
                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled,
                                                    [d boolForKey:kSettingsGravityLiteEnabled]);
                        log_user("[OK] Gravity Lite armed. On the Home Screen, shake hard twice to start physics; shake hard twice again to restore.\n");
                        cyanide_upload_log_milestone(@"gravity-lite-shake-armed");'''
    # Keep the original else-if marker in place.
    a = text.find(run_start)
    if a < 0:
        fail("start marker not found: Run Gravity Lite block")
    b = text.find(run_end, a)
    if b < 0:
        fail("end marker not found: Run Gravity Lite block")
    text = text[:a] + run_replacement.rstrip() + "\n" + text[b:]

    # Manual Restore should restore icons but leave the double-shake feature
    # armed when its master switch is still enabled.
    old_restore = '''                if (restore) {
                    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
                    settings_stop_gravity_motion();
                    settings_mark_tweak_applied(kSettingsGravityLiteEnabled, NO);'''
    new_restore = '''                if (restore) {
                    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
                    __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                    if ([d boolForKey:kSettingsGravityLiteEnabled] && g_springboard_rc_ready) {
                        GravityLiteConfig rearmConfig = settings_gravitylite_config_from_defaults(d);
                        settings_start_gravity_motion(rearmConfig.magnitude,
                                                      rearmConfig.explosionForce);
                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);
                    } else {
                        settings_stop_gravity_motion();
                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled, NO);
                    }'''
    if old_restore in text:
        text = text.replace(old_restore, new_restore, 1)

    # Locking tears down the remote Gravity pointers in the stock code. Clear
    # our local active bit at the same moment so unlock does not waste tilt
    # RemoteCalls against an empty pointer cache.
    old_lock = '''                settings_stop_gravity_motion();
                gravitylite_forget_remote_state();'''
    new_lock = '''                __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                settings_stop_gravity_motion();
                gravitylite_forget_remote_state();'''
    if old_lock in text:
        text = text.replace(old_lock, new_lock, 1)

    # Update the in-app help text so it no longer claims automatic shake is absent.
    old_help = '''Not included in this core port: Activator/Home-button hooks, drag gestures, automatic shake effects, and preference-daemon notifications.'''
    new_help = '''Shake gesture: while Gravity Lite is enabled and Cyanide remains alive in the background, two deliberate hard shakes start icon physics. While physics is active, swiping to another Home Screen page automatically restores the previous page and hands physics to the newly visible page while preserving Dock physics. Two more hard shakes animate icons smoothly back to their saved grid positions before cleanup. Physical icons remain intentionally non-interactive; Activator/Home-button hooks, drag gestures, and preference-daemon notifications are still not included.'''
    if old_help in text:
        text = text.replace(old_help, new_help, 1)

    # Manual Gravity Restore uses the same accelerated transaction.
    text = text.replace(
        "? gravitylite_stop_in_session()\n                            : gravitylite_explosion_in_session",
        "? settings_gravity_restore_quick()\n                            : gravitylite_explosion_in_session"
    )

    gravity_path, gravity_text, header_path, header_text = patch_gravity_core(root)

    # All anchors validated. Write Settings + Gravity core together only now.
    path.write_text(text, encoding="utf-8")
    gravity_path.write_text(gravity_text, encoding="utf-8")
    header_path.write_text(header_text, encoding="utf-8")
    print("[GRAVITY-SHAKE] v3 page-handoff + animated-restore patched SettingsViewController.m + gravitylite core")
    print("[GRAVITY-SHAKE] keep-alive audio is reused from Cyanide's existing DSKeepAlive; no new audio session is added")


if __name__ == "__main__":
    main()
