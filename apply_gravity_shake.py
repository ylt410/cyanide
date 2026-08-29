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

    if "gravitylite_suspend_home_group_in_session" in g and "gl_build_true_local_group" in g:
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
        '// User-facing restore uses a smooth absorb-to-grid animation.\n'
        'bool gravitylite_stop_in_session(void);\n'
        '// Immediate stale-state cleanup, used before a new physics session.\n'
        'bool gravitylite_stop_immediate_in_session(void);\n'
        '// Current Home Screen list-view token. The app treats it as opaque.\n'
        'uint64_t gravitylite_current_page_token_in_session(void);\n'
        '// Quantized window-space X position of a page token; 0 means unavailable.\n'
        'uint64_t gravitylite_page_x_signature_in_session(uint64_t pageToken);\n'
        '// Detect page motion before SpringBoard flips the current-page token.\n'
        'bool gravitylite_home_group_is_displaced_in_session(double threshold);\n'
        '// Page-local true-icon mode: stop Home physics and animate the real icons\n'
        '// back to their native grid without changing their superview hierarchy.\n'
        'bool gravitylite_suspend_home_group_in_session(double duration);\n'
        '// After paging settles, attach UIDynamics to the true icons of the stable page.\n'
        'bool gravitylite_resume_current_page_in_session(GravityLiteConfig config, uint64_t expectedPageToken);\n'
        'bool gravitylite_explosion_in_session(double force);',
        'gravitylite.h true-icon page API')

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

    true_builder = r'''static bool gl_build_true_local_group(uint64_t groups,
                                      uint64_t listView,
                                      uint64_t iconViewCls,
                                      GravityLiteConfig config,
                                      bool isDock,
                                      bool useIOS26Path)
{
    // The old live-icon implementation moved SBIconViews into a window-level
    // overlay. That looked perfect but fought SpringBoard paging and could
    // restart SpringBoard during fast swipes. This version keeps every icon in
    // its original hierarchy and makes UIDynamics reference the icon container
    // that already owns those views.
    enum { ICON_CAP = 256, PARENT_CAP = 16 };
    uint64_t iconViews[ICON_CAP] = {0};
    int iconCount = 0;

    uint32_t oldCollectSettle = r_settle_us(0);
    if (useIOS26Path) {
        iconCount = sb_collect_views(listView, iconViewCls, iconViews, ICON_CAP);
    }
    if (iconCount <= 0) {
        iconCount = gl_icon_views_from_list(listView, iconViewCls, iconViews, ICON_CAP);
    }
    r_settle_us(oldCollectSettle);
    if (iconCount <= 0) return false;

    // Pick the parent that owns the largest number of visible SBIconViews.
    // SpringBoard normally keeps one grid container per icon-list page, but
    // counting instead of assuming makes the code tolerate extra wrappers.
    uint64_t parents[PARENT_CAP] = {0};
    int parentCounts[PARENT_CAP] = {0};
    int parentSlots = 0;
    for (int i = 0; i < iconCount; i++) {
        uint64_t icon = iconViews[i];
        if (!r_is_objc_ptr(icon) || gl_view_is_hidden(icon)) continue;
        uint64_t parent = gl_safe_msg(icon, "superview", 0, 0, 0, 0);
        if (!r_is_objc_ptr(parent)) continue;
        int slot = -1;
        for (int p = 0; p < parentSlots; p++) {
            if (parents[p] == parent) { slot = p; break; }
        }
        if (slot < 0 && parentSlots < PARENT_CAP) {
            slot = parentSlots++;
            parents[slot] = parent;
        }
        if (slot >= 0) parentCounts[slot]++;
    }

    int bestSlot = -1;
    int bestCount = 0;
    for (int p = 0; p < parentSlots; p++) {
        if (parentCounts[p] > bestCount) {
            bestCount = parentCounts[p];
            bestSlot = p;
        }
    }
    if (bestSlot < 0 || bestCount <= 0) return false;
    uint64_t referenceView = parents[bestSlot];

    uint64_t icons = gl_new_remote("NSMutableArray");
    uint64_t originalFrames = gl_new_remote("NSMutableArray");
    if (!r_is_objc_ptr(icons) || !r_is_objc_ptr(originalFrames)) {
        if (icons) gl_release(icons);
        if (originalFrames) gl_release(originalFrames);
        return false;
    }

    int added = 0;
    uint32_t oldSettle = r_settle_us(0);
    for (int i = 0; i < iconCount; i++) {
        uint64_t icon = iconViews[i];
        if (!r_is_objc_ptr(icon) || gl_view_is_hidden(icon)) continue;
        uint64_t parent = gl_safe_msg(icon, "superview", 0, 0, 0, 0);
        if (parent != referenceView) continue;

        GL_CGRect frame;
        if (!gl_get_rect(icon, "frame", &frame) || !gl_rect_valid(frame)) continue;
        uint64_t frameValue = gl_value_with_rect(frame);
        if (!r_is_objc_ptr(frameValue)) continue;

        // No addSubview:, no alpha swap, no snapshot. The icon, title, badge,
        // folder material and every private SpringBoard subview remain intact.
        gl_reset_transform(icon);
        gl_array_add(icons, icon);
        gl_array_add(originalFrames, frameValue);
        added++;
    }
    r_settle_us(oldSettle);

    if (added <= 0) {
        gl_release(icons);
        gl_release(originalFrames);
        return false;
    }

    uint64_t animator = gl_animator_for_reference_view(referenceView);
    if (!r_is_objc_ptr(animator)) {
        gl_release(icons);
        gl_release(originalFrames);
        return false;
    }

    uint64_t collision = gl_alloc_init_with_items("UICollisionBehavior", icons);
    if (r_is_objc_ptr(collision)) {
        gl_set_bool(collision, "setTranslatesReferenceBoundsIntoBoundary:", true);
        if (r_responds_main(collision, "setCollisionMode:")) {
            r_msg2_main(collision, "setCollisionMode:", 3, 0, 0, 0);
        }
        r_msg2_main(animator, "addBehavior:", collision, 0, 0, 0);
        gl_release(collision);
    }

    uint64_t itemBehavior = gl_alloc_init_with_items("UIDynamicItemBehavior", icons);
    if (r_is_objc_ptr(itemBehavior)) {
        gl_set_double(itemBehavior, "setElasticity:", config.bounce);
        gl_set_double(itemBehavior, "setFriction:", config.friction);
        gl_set_double(itemBehavior, "setDensity:", 1.0);
        gl_set_double(itemBehavior, "setResistance:", config.resistance);
        gl_set_double(itemBehavior, "setAngularResistance:", config.angularResistance);
        gl_set_bool(itemBehavior, "setAllowsRotation:", config.allowsRotation);
        r_msg2_main(animator, "addBehavior:", itemBehavior, 0, 0, 0);
        gl_release(itemBehavior);
    }

    uint64_t gravity = gl_alloc_init_with_items("UIGravityBehavior", icons);
    if (r_is_objc_ptr(gravity)) {
        gl_set_double(gravity, "setAngle:", M_PI_2);
        gl_set_double(gravity, "setMagnitude:", config.magnitude);
        r_msg2_main(animator, "addBehavior:", gravity, 0, 0, 0);
        int n = __atomic_load_n(&s_gravity_ptr_count, __ATOMIC_RELAXED);
        if (n < 8) {
            s_gravity_ptrs[n] = gravity;
            __atomic_store_n(&s_gravity_ptr_count, n + 1, __ATOMIC_SEQ_CST);
        }
        gl_release(gravity);
    }

    uint64_t baselineValue = 0;
    if (!isDock) {
        uint64_t window = gl_view_window(listView);
        GL_CGRect bounds;
        GL_CGRect inWindow;
        if (r_is_objc_ptr(window) &&
            gl_get_rect(listView, "bounds", &bounds) &&
            gl_rect_valid(bounds) &&
            gl_convert_rect_to_view(listView, bounds, window, &inWindow) &&
            gl_rect_valid(inWindow)) {
            baselineValue = gl_value_with_rect(inWindow);
        }
    }

    uint64_t group = gl_new_remote("NSMutableDictionary");
    if (!r_is_objc_ptr(group)) {
        r_msg2_main(animator, "removeAllBehaviors", 0, 0, 0, 0);
        gl_release(animator);
        gl_release(icons);
        gl_release(originalFrames);
        return false;
    }

    gl_dict_set(group, "animator", animator);
    gl_dict_set(group, "icons", icons);
    // Keep the stock explosion path compatible; these are real views now.
    gl_dict_set(group, "snapshots", icons);
    gl_dict_set(group, "liveFrames", originalFrames);
    gl_dict_set(group, "gravity", gravity);
    gl_dict_set(group, "listView", listView);
    gl_dict_set(group, "referenceView", referenceView);
    if (r_is_objc_ptr(baselineValue)) gl_dict_set(group, "pageWindowFrame", baselineValue);
    gl_array_add(groups, group);

    uint64_t isRunning = gl_safe_msg(animator, "isRunning", 0, 0, 0, 0);
    printf("[GRAVITY] True-icon %s group: %d/%d item(s), native parent=0x%llx, physics=%s\n",
           isDock ? "dock" : "home",
           added, iconCount,
           (unsigned long long)referenceView,
           isRunning ? "running" : "starting");

    gl_release(group);
    gl_release(animator);
    gl_release(icons);
    gl_release(originalFrames);
    return true;
}

static bool gl_build_group(uint64_t groups,
                           uint64_t listView,
                           uint64_t iconViewCls,
                           GravityLiteConfig config,
                           bool isDock,
                           bool useIOS26Path)
{
    return gl_build_true_local_group(groups,
                                     listView,
                                     iconViewCls,
                                     config,
                                     isDock,
                                     useIOS26Path);
}
'''
    g = replace_region(
        g,
        'static bool gl_build_group_ios26_per_icon(',
        'bool gravitylite_stop_in_session(void)\n{',
        true_builder,
        'replace overlay/snapshot builders with page-local true icons')

    restore_block = r'''static int gl_restore_group_true_local(uint64_t group,
                                       double duration,
                                       bool waitForCompletion)
{
    if (!r_is_objc_ptr(group)) return 0;

    uint64_t animator = gl_dict_get(group, "animator");
    uint64_t icons = gl_dict_get(group, "icons");
    uint64_t frames = gl_dict_get(group, "liveFrames");
    uint64_t listView = gl_dict_get(group, "listView");

    if (r_is_objc_ptr(animator)) {
        r_msg2_main(animator, "removeAllBehaviors", 0, 0, 0, 0);
    }

    uint64_t count = gl_array_count(icons);
    uint64_t frameCount = gl_array_count(frames);
    if (count > frameCount) count = frameCount;
    if (count > 256) count = 256;

    bool animate = duration > 0.01;
    uint64_t UIView = animate ? r_class("UIView") : 0;
    if (!r_is_objc_ptr(UIView) ||
        !r_responds_main(UIView, "beginAnimations:context:") ||
        !r_responds_main(UIView, "setAnimationDuration:") ||
        !r_responds_main(UIView, "commitAnimations")) {
        animate = false;
    }

    if (animate) {
        r_msg2_main(UIView, "beginAnimations:context:", 0, 0, 0, 0);
        gl_set_double(UIView, "setAnimationDuration:", duration);
        gl_set_integer(UIView, "setAnimationCurve:", 0);
        gl_set_bool(UIView, "setAnimationBeginsFromCurrentState:", true);
    }

    int restored = 0;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t icon = gl_array_object(icons, i);
        uint64_t value = gl_array_object(frames, i);
        GL_CGRect target;
        if (!r_is_objc_ptr(icon) ||
            !gl_rect_from_value(value, &target) ||
            !gl_rect_valid(target)) {
            continue;
        }
        gl_reset_transform(icon);
        gl_set_rect(icon, "setFrame:", target);
        restored++;
    }

    if (animate) {
        r_msg2_main(UIView, "commitAnimations", 0, 0, 0, 0);
    }

    // Compatibility cleanup for a stale pre-v5 session that may still be
    // attached to SpringBoard when the new IPA is cover-installed.
    uint64_t legacyItems = gl_dict_get(group, "liveItems");
    uint64_t legacyParents = gl_dict_get(group, "liveParents");
    uint64_t legacyFrames = gl_dict_get(group, "liveFrames");
    if (r_is_objc_ptr(legacyItems) && r_is_objc_ptr(legacyParents)) {
        restored += gl_restore_live_items(legacyItems, legacyParents, legacyFrames);
    }
    uint64_t originalIcons = gl_dict_get(group, "originalIcons");
    uint64_t sources = gl_dict_get(group, "sources");
    restored += gl_unhide_icon_array(originalIcons);
    gl_set_array_views_alpha(sources, 1.0);
    if (r_is_objc_ptr(listView)) gl_set_double(listView, "setAlpha:", 1.0);
    uint64_t overlay = gl_dict_get(group, "overlay");
    if (r_is_objc_ptr(overlay)) {
        r_msg2_main(overlay, "removeFromSuperview", 0, 0, 0, 0);
    }

    if (animate && waitForCompletion) {
        unsigned int waitUS = (unsigned int)(duration * 1000000.0) + 60000U;
        usleep(waitUS);
        // A final native layout after the absorb animation guarantees exact
        // grid alignment without killing the animation on its first frame.
        if (r_is_objc_ptr(listView)) gl_layout_list_view(listView);
    }
    return restored;
}

static void gl_rebuild_gravity_ptr_cache(uint64_t groups)
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

static bool gravitylite_stop_internal(bool animated)
{
    uint64_t ctrl = gl_icon_controller();
    if (!r_is_objc_ptr(ctrl)) {
        printf("[GRAVITY] stop: SBIconController missing\n");
        return false;
    }

    uint64_t state = gl_get_state(ctrl);
    if (!r_is_objc_ptr(state)) {
        __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);
        memset(s_gravity_ptrs, 0, sizeof(s_gravity_ptrs));
        int orphans = gl_cleanup_gravity_overlays_in_app_windows();
        if (orphans > 0)
            printf("[GRAVITY] stop: removed %d stale overlay(s).\n", orphans);
        return true;
    }

    uint64_t groups = gl_dict_get(state, "groups");
    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    int restored = 0;
    double duration = animated ? 0.48 : 0.0;
    for (uint64_t i = 0; i < count; i++) {
        restored += gl_restore_group_true_local(gl_array_object(groups, i),
                                                duration,
                                                false);
    }

    if (animated && count > 0) {
        usleep(540000);
        for (uint64_t i = 0; i < count; i++) {
            uint64_t group = gl_array_object(groups, i);
            uint64_t listView = gl_dict_get(group, "listView");
            if (r_is_objc_ptr(listView)) gl_layout_list_view(listView);
        }
    }

    gl_set_state(ctrl, 0);
    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);
    memset(s_gravity_ptrs, 0, sizeof(s_gravity_ptrs));
    int orphans = gl_cleanup_gravity_overlays_in_app_windows();
    printf("[GRAVITY] Restored %d true icon(s)%s; stale overlays=%d.\n",
           restored, animated ? " with absorb animation" : "", orphans);
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
}
'''
    g = replace_region(
        g,
        'bool gravitylite_stop_in_session(void)\n{',
        'bool gravitylite_apply_in_session(GravityLiteConfig config)',
        restore_block,
        'true-icon restore engine')

    g = replace_once(
        g,
        '    (void)gravitylite_stop_in_session();\n'
        '    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);',
        '    (void)gravitylite_stop_immediate_in_session();\n'
        '    __atomic_store_n(&s_gravity_ptr_count, 0, __ATOMIC_SEQ_CST);',
        'gravity apply immediate stale cleanup')

    # Logs/help inside the stock apply path still describe the old snapshot/live
    # split. Physics is true-icon on every supported path now; list resolution
    # remains OS-specific only because SpringBoard's private APIs differ.
    g = g.replace(
        '    printf("[GRAVITY] Using iOS %d %s path.\\n",\n'
        '           iosMajor > 0 ? iosMajor : 0,\n'
        '           useLiveIconPath\n'
        '               ? "live icon"\n'
        '               : "snapshot");',
        '    printf("[GRAVITY] Using iOS %d page-local TRUE ICON path (native hierarchy preserved).\\n",\n'
        '           iosMajor > 0 ? iosMajor : 0);')
    g = g.replace('Capturing home screen icon snapshots...', 'Capturing home screen true icons...')
    g = g.replace('Capturing dock icon snapshots...', 'Capturing dock true icons...')
    g = g.replace('"snapshot"', '"true-icon"')

    page_api = r'''uint64_t gravitylite_current_page_token_in_session(void)
{
    uint32_t oldSettle = r_settle_us(0);
    uint64_t token = 0;
    uint64_t ctrl = gl_icon_controller();
    if (r_is_objc_ptr(ctrl)) {
        uint64_t mgr = gl_icon_manager(ctrl);
        uint64_t iconViewCls = r_class("SBIconView");
        int iosMajor = gl_remote_ios_major();
        bool useLiveListResolver = (iosMajor >= 26 || iosMajor == 17);
        uint64_t listView = useLiveListResolver
            ? gl_current_root_list_view_ios26_legacy(ctrl)
            : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
        if (!r_is_objc_ptr(listView)) listView = gl_current_root_list_view(ctrl, mgr);
        if (r_is_objc_ptr(listView)) token = listView;
    }
    r_settle_us(oldSettle);
    return token;
}

uint64_t gravitylite_page_x_signature_in_session(uint64_t pageToken)
{
    if (!r_is_objc_ptr(pageToken)) return 0;
    uint32_t oldSettle = r_settle_us(0);
    uint64_t signature = 0;
    uint64_t window = gl_view_window(pageToken);
    GL_CGRect bounds;
    GL_CGRect inWindow;
    if (r_is_objc_ptr(window) &&
        gl_get_rect(pageToken, "bounds", &bounds) &&
        gl_rect_valid(bounds) &&
        gl_convert_rect_to_view(pageToken, bounds, window, &inWindow) &&
        gl_rect_valid(inWindow)) {
        // Quarter-point quantization ignores tiny render jitter but catches the
        // first meaningful horizontal paging movement. Offset keeps x=0 valid
        // while reserving 0 as the unavailable sentinel.
        int64_t q = (int64_t)llround(inWindow.x * 4.0);
        signature = (uint64_t)(q + 0x4000000000000000LL);
        if (signature == 0) signature = 1;
    }
    r_settle_us(oldSettle);
    return signature;
}

static bool gl_group_is_dock(uint64_t group,
                             uint64_t dockListView)
{
    uint64_t listView = gl_dict_get(group, "listView");
    return r_is_objc_ptr(dockListView) && listView == dockListView;
}

bool gravitylite_home_group_is_displaced_in_session(double threshold)
{
    if (threshold < 0.5) threshold = 0.5;
    uint32_t oldSettle = r_settle_us(0);
    bool displaced = false;

    uint64_t ctrl = gl_icon_controller();
    uint64_t state = r_is_objc_ptr(ctrl) ? gl_get_state(ctrl) : 0;
    if (!r_is_objc_ptr(ctrl) || !r_is_objc_ptr(state)) goto done;
    uint64_t groups = gl_dict_get(state, "groups");
    uint64_t mgr = gl_icon_manager(ctrl);
    int iosMajor = gl_remote_ios_major();
    bool useLiveListResolver = (iosMajor >= 26 || iosMajor == 17);
    uint64_t dockListView = gl_dock_list_view_for_path(ctrl, mgr, useLiveListResolver);

    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t group = gl_array_object(groups, i);
        if (gl_group_is_dock(group, dockListView)) continue;
        uint64_t listView = gl_dict_get(group, "listView");
        uint64_t baselineValue = gl_dict_get(group, "pageWindowFrame");
        GL_CGRect baseline;
        if (!r_is_objc_ptr(listView) ||
            !gl_rect_from_value(baselineValue, &baseline) ||
            !gl_rect_valid(baseline)) {
            continue;
        }

        uint64_t window = gl_view_window(listView);
        GL_CGRect bounds;
        GL_CGRect current;
        if (!r_is_objc_ptr(window) ||
            !gl_get_rect(listView, "bounds", &bounds) ||
            !gl_rect_valid(bounds) ||
            !gl_convert_rect_to_view(listView, bounds, window, &current) ||
            !gl_rect_valid(current)) {
            displaced = true;
            break;
        }
        if (fabs(current.x - baseline.x) > threshold ||
            fabs(current.y - baseline.y) > threshold) {
            displaced = true;
            break;
        }
        break;
    }

done:
    r_settle_us(oldSettle);
    return displaced;
}

bool gravitylite_suspend_home_group_in_session(double duration)
{
    if (duration < 0.0) duration = 0.0;
    if (duration > 0.35) duration = 0.35;
    uint32_t oldSettle = r_settle_us(0);
    bool ok = false;

    uint64_t ctrl = gl_icon_controller();
    uint64_t state = r_is_objc_ptr(ctrl) ? gl_get_state(ctrl) : 0;
    if (!r_is_objc_ptr(ctrl) || !r_is_objc_ptr(state)) goto done;
    uint64_t groups = gl_dict_get(state, "groups");
    if (!r_is_objc_ptr(groups)) goto done;

    uint64_t mgr = gl_icon_manager(ctrl);
    int iosMajor = gl_remote_ios_major();
    bool useLiveListResolver = (iosMajor >= 26 || iosMajor == 17);
    uint64_t dockListView = gl_dock_list_view_for_path(ctrl, mgr, useLiveListResolver);
    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;

    int removed = 0;
    for (int64_t i = (int64_t)count - 1; i >= 0; i--) {
        uint64_t group = gl_array_object(groups, (uint64_t)i);
        if (gl_group_is_dock(group, dockListView)) continue;
        (void)gl_restore_group_true_local(group, duration, false);
        gl_array_remove_at(groups, (uint64_t)i);
        removed++;
    }
    gl_rebuild_gravity_ptr_cache(groups);
    ok = true;
    if (removed > 0) {
        printf("[GRAVITY] Paging began: %d home group(s) absorbing to grid over %.2fs; Dock preserved.\n",
               removed, duration);
    }

done:
    r_settle_us(oldSettle);
    return ok;
}

bool gravitylite_resume_current_page_in_session(GravityLiteConfig config,
                                                uint64_t expectedPageToken)
{
    uint32_t oldSettle = r_settle_us(0);
    bool ok = false;

    uint64_t ctrl = gl_icon_controller();
    uint64_t state = r_is_objc_ptr(ctrl) ? gl_get_state(ctrl) : 0;
    if (!r_is_objc_ptr(ctrl) || !r_is_objc_ptr(state) || expectedPageToken == 0) goto done;
    uint64_t groups = gl_dict_get(state, "groups");
    if (!r_is_objc_ptr(groups)) goto done;

    uint64_t mgr = gl_icon_manager(ctrl);
    uint64_t iconViewCls = r_class("SBIconView");
    if (!r_is_objc_ptr(iconViewCls)) goto done;
    int iosMajor = gl_remote_ios_major();
    bool useLiveListResolver = (iosMajor >= 26 || iosMajor == 17);
    uint64_t dockListView = gl_dock_list_view_for_path(ctrl, mgr, useLiveListResolver);

    uint64_t desiredPage = useLiveListResolver
        ? gl_current_root_list_view_ios26_legacy(ctrl)
        : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
    if (!r_is_objc_ptr(desiredPage)) desiredPage = gl_current_root_list_view(ctrl, mgr);
    if (!r_is_objc_ptr(desiredPage) || desiredPage != expectedPageToken) goto done;

    uint64_t count = gl_array_count(groups);
    if (count > 64) count = 64;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t group = gl_array_object(groups, i);
        uint64_t listView = gl_dict_get(group, "listView");
        if (!gl_group_is_dock(group, dockListView) && listView == desiredPage) {
            gl_rebuild_gravity_ptr_cache(groups);
            ok = true;
            goto done;
        }
    }

    // Defensive cleanup: a stale non-dock group should never survive the
    // suspend phase, but remove it without animation rather than stacking two
    // UIDynamicAnimators on Home Screen icons.
    for (int64_t i = (int64_t)count - 1; i >= 0; i--) {
        uint64_t group = gl_array_object(groups, (uint64_t)i);
        if (gl_group_is_dock(group, dockListView)) continue;
        (void)gl_restore_group_true_local(group, 0.0, false);
        gl_array_remove_at(groups, (uint64_t)i);
    }
    gl_rebuild_gravity_ptr_cache(groups);

    // Re-check immediately before mutation. A fast second swipe makes this
    // fail cleanly instead of attaching physics to a page that is already gone.
    desiredPage = useLiveListResolver
        ? gl_current_root_list_view_ios26_legacy(ctrl)
        : gl_find_home_icon_list_view(ctrl, mgr, iconViewCls, false);
    if (!r_is_objc_ptr(desiredPage)) desiredPage = gl_current_root_list_view(ctrl, mgr);
    if (!r_is_objc_ptr(desiredPage) || desiredPage != expectedPageToken) goto done;

    ok = gl_build_group(groups,
                        desiredPage,
                        iconViewCls,
                        config,
                        false,
                        useLiveListResolver);
    if (!ok) goto done;

    // One last token check after the relatively expensive view collection.
    uint64_t after = gravitylite_current_page_token_in_session();
    if (after != expectedPageToken) {
        uint64_t newCount = gl_array_count(groups);
        for (int64_t i = (int64_t)newCount - 1; i >= 0; i--) {
            uint64_t group = gl_array_object(groups, (uint64_t)i);
            uint64_t listView = gl_dict_get(group, "listView");
            if (!gl_group_is_dock(group, dockListView) && listView == expectedPageToken) {
                (void)gl_restore_group_true_local(group, 0.0, false);
                gl_array_remove_at(groups, (uint64_t)i);
            }
        }
        gl_rebuild_gravity_ptr_cache(groups);
        ok = false;
        goto done;
    }

    printf("[GRAVITY] Stable page 0x%llx resumed with page-local TRUE ICON physics.\n",
           (unsigned long long)expectedPageToken);

done:
    r_settle_us(oldSettle);
    return ok;
}

'''
    g = replace_once(
        g,
        'bool gravitylite_explosion_in_session(double force)\n{',
        page_api + 'bool gravitylite_explosion_in_session(double force)\n{',
        'true-icon page suspend/resume API')

    required = (
        'gl_build_true_local_group',
        'gravitylite_suspend_home_group_in_session',
        'gravitylite_resume_current_page_in_session',
        'gravitylite_home_group_is_displaced_in_session',
        'gravitylite_page_x_signature_in_session',
    )
    for marker in required:
        if marker not in g:
            fail(f"compile-safety guard failed: missing generated gravity marker {marker}")

    return gravity_path, g, header_path, h

def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    path = root / "Cyanide" / "SettingsViewController.m"
    if not path.exists():
        fail(f"missing {path}")
    text = path.read_text(encoding="utf-8")

    # Idempotence: do not stack the patch if the workflow is re-run on an already patched tree.
    if "settings_gravity_submit_tilt_async" in text and "Double-shake detector armed" in text and "gravitylite_suspend_home_group_in_session" in (root / "Cyanide" / "tweaks" / "gravitylite.m").read_text(encoding="utf-8"):
        print("[GRAVITY-SHAKE] v5 true-icon already applied")
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
// While physics is active, poll the current SpringBoard icon page separately
// from CoreMotion. Real SBIconViews stay in their native page hierarchy. At
// swipe start they absorb back to the grid, then the stable destination page
// receives a fresh page-local UIDynamics session.
static volatile uint64_t g_gravity_page_probe_after_us = 0;
static volatile uint64_t g_gravity_page_token = 0;
static volatile uint64_t g_gravity_page_candidate_token = 0;
static volatile uint64_t g_gravity_page_candidate_since_us = 0;
static volatile uint64_t g_gravity_page_position_signature = 0;
static volatile int g_gravity_page_visuals_suspended = 0;
static volatile int g_gravity_page_handoff_running = 0;
// Never block the CoreMotion callback on a SpringBoard RemoteCall. While
// physics is active we submit at most one tilt update worker at a time and
// throttle it to ~10 Hz, leaving room for the page-stability probe and the
// 25 Hz sensor feed without building a RemoteCall backlog.
static volatile int g_gravity_tilt_update_running = 0;
static volatile uint64_t g_gravity_tilt_next_submit_us = 0;'''
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
    __sync_lock_test_and_set(&g_gravity_page_candidate_token, 0);
    __sync_lock_test_and_set(&g_gravity_page_candidate_since_us, 0);
    __sync_lock_test_and_set(&g_gravity_page_position_signature, 0);
    __sync_lock_test_and_set(&g_gravity_page_visuals_suspended, 0);
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


static void settings_gravity_submit_tilt_async(double angle,
                                               double magnitude,
                                               uint64_t generation,
                                               CMMotionManager *manager)
{
    if (g_gravitylite_physics_active == 0 ||
        g_gravity_shake_toggle_running != 0 ||
        g_gravity_motion_stop_requested != 0 ||
        !g_springboard_rc_ready ||
        settings_cleanup_in_progress()) {
        return;
    }

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0 || now < g_gravity_tilt_next_submit_us) return;
    __sync_lock_test_and_set(&g_gravity_tilt_next_submit_us, now + 100000ULL);

    // If the previous RemoteCall is still in flight, drop this tilt sample.
    // Fresh sensor samples are more valuable than building a stale backlog.
    if (__sync_lock_test_and_set(&g_gravity_tilt_update_running, 1)) return;

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @try {
            if (!settings_gravity_motion_can_remote_call(generation, manager) ||
                g_gravitylite_physics_active == 0 ||
                g_gravity_shake_toggle_running != 0) {
                return;
            }

            @synchronized (settings_rc_lock()) {
                if (!settings_gravity_motion_can_remote_call(generation, manager) ||
                    g_gravitylite_physics_active == 0 ||
                    g_gravity_shake_toggle_running != 0) {
                    return;
                }
                uint32_t oldSettle = r_settle_us(0);
                gravitylite_update_gravity_angle_in_session(angle, magnitude);
                r_settle_us(oldSettle);
            }
        } @finally {
            __sync_lock_release(&g_gravity_tilt_update_running);
        }
    });
}

static void settings_gravity_maybe_handoff_page_async(void)
{
    if (g_gravitylite_physics_active == 0 ||
        g_gravity_shake_toggle_running != 0 ||
        g_gravity_motion_stop_requested != 0 ||
        !g_springboard_rc_ready ||
        settings_cleanup_in_progress()) {
        return;
    }

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0 || now < g_gravity_page_probe_after_us) return;
    // 20 Hz is fast enough to notice the icon-list sliding before the selected
    // page token usually flips, without turning CoreMotion into a RemoteCall
    // denial-of-service attack against SpringBoard.
    __sync_lock_test_and_set(&g_gravity_page_probe_after_us, now + 50000ULL);
    if (__sync_lock_test_and_set(&g_gravity_page_handoff_running, 1)) return;

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @try {
            NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
            @synchronized (settings_rc_lock()) {
                if (g_gravitylite_physics_active == 0 ||
                    g_gravity_shake_toggle_running != 0 ||
                    g_gravity_motion_stop_requested != 0 ||
                    !g_springboard_rc_ready ||
                    settings_cleanup_in_progress() ||
                    ![d boolForKey:kSettingsGravityLiteEnabled]) {
                    return;
                }

                uint32_t oldSettle = r_settle_us(0);
                uint64_t sampleNow = settings_gravity_sensor_now_us();
                uint64_t token = gravitylite_current_page_token_in_session();
                uint64_t active = g_gravity_page_token;
                uint64_t positionSignature = gravitylite_page_x_signature_in_session(token);
                if (token == 0 || positionSignature == 0) {
                    r_settle_us(oldSettle);
                    return;
                }

                bool displaced = gravitylite_home_group_is_displaced_in_session(2.0);

                if (g_gravity_page_visuals_suspended == 0) {
                    uint64_t lastPosition = g_gravity_page_position_signature;
                    bool positionMoved = (lastPosition != 0 && positionSignature != lastPosition);
                    if (active != 0 && token == active && !displaced && !positionMoved) {
                        // Ordinary stationary frame; keep the quantized baseline fresh.
                        __sync_lock_test_and_set(&g_gravity_page_position_signature, positionSignature);
                        r_settle_us(oldSettle);
                        return;
                    }

                    // Either the list has begun moving or SpringBoard has already
                    // switched the page token. Remove dynamics first, then animate
                    // the REAL icons back to their saved frames. No reparenting.
                    bool suspended = gravitylite_suspend_home_group_in_session(0.22);
                    if (!suspended) {
                        r_settle_us(oldSettle);
                        return;
                    }
                    __sync_lock_test_and_set(&g_gravity_page_visuals_suspended, 1);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_token, token);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_since_us, sampleNow);
                    __sync_lock_test_and_set(&g_gravity_page_position_signature, positionSignature);
                    printf("[GRAVITY] paging detected; true icons absorbing to grid token=0x%llx\n",
                           (unsigned long long)token);
                    r_settle_us(oldSettle);
                    return;
                }

                // Suspended: every movement or page-token change restarts the
                // stability timer. Fast multi-page flicks therefore do zero
                // intermediate physics builds.
                uint64_t candidate = g_gravity_page_candidate_token;
                uint64_t lastPosition = g_gravity_page_position_signature;
                if (candidate != token || lastPosition == 0 || positionSignature != lastPosition) {
                    __sync_lock_test_and_set(&g_gravity_page_candidate_token, token);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_since_us, sampleNow);
                    __sync_lock_test_and_set(&g_gravity_page_position_signature, positionSignature);
                    r_settle_us(oldSettle);
                    return;
                }

                uint64_t since = g_gravity_page_candidate_since_us;
                // 380 ms comfortably outlasts the 220 ms absorb animation while
                // still making the next page feel immediate after paging stops.
                if (since == 0 || sampleNow < since || sampleNow - since < 380000ULL) {
                    r_settle_us(oldSettle);
                    return;
                }

                GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
                bool ok = gravitylite_resume_current_page_in_session(config, token);
                uint64_t current = ok ? gravitylite_current_page_token_in_session() : 0;
                if (ok && current == token) {
                    __sync_lock_test_and_set(&g_gravity_page_token, current);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_token, 0);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_since_us, 0);
                    __sync_lock_test_and_set(&g_gravity_page_position_signature,
                                             gravitylite_page_x_signature_in_session(current));
                    __sync_lock_test_and_set(&g_gravity_page_visuals_suspended, 0);
                    printf("[GRAVITY] true-icon physics resumed after stable paging token=0x%llx\n",
                           (unsigned long long)current);
                } else {
                    uint64_t latest = gravitylite_current_page_token_in_session();
                    __sync_lock_test_and_set(&g_gravity_page_candidate_token, latest);
                    __sync_lock_test_and_set(&g_gravity_page_candidate_since_us,
                                             settings_gravity_sensor_now_us());
                    __sync_lock_test_and_set(&g_gravity_page_position_signature,
                                             gravitylite_page_x_signature_in_session(latest));
                    printf("[GRAVITY] true-icon resume deferred; latest=0x%llx\n",
                           (unsigned long long)latest);
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
        // OFF must preempt the continuous tilt stream. Otherwise the restore
        // worker can sit behind RemoteCall traffic while the user keeps
        // shaking a phone that has already heard the gesture.
        if (!turningOn) {
            __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
        }
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
                        uint64_t initialPageToken = gravitylite_current_page_token_in_session();
                        __sync_lock_test_and_set(&g_gravity_page_token, initialPageToken);
                        __sync_lock_test_and_set(&g_gravity_page_position_signature,
                                                 gravitylite_page_x_signature_in_session(initialPageToken));
                        __sync_lock_test_and_set(&g_gravity_page_probe_after_us,
                                                 settings_gravity_sensor_now_us() + 100000ULL);
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
                    } else {
                        // Restore failed. Resume physics/tilt state rather than
                        // leaving the local toggle bit falsely OFF.
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 1);
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
    const uint64_t forcedReleaseUS = 420000ULL;
    const uint64_t doubleShakeWindowUS = (g_gravitylite_physics_active != 0)
                                       ? 2400000ULL : 1800000ULL;
    const uint64_t postToggleCooldownUS = 1200000ULL;

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0) return;
    if (now < g_gravity_shake_cooldown_until_us) return;

    if (a <= release) {
        __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 0);
        return;
    }

    uint64_t last = g_gravity_shake_last_pulse_us;
    if (g_gravity_shake_waiting_for_release != 0) {
        // A very hard shake can keep userAcceleration above the old release
        // threshold between two swings. Re-arm after a conservative gap so
        // "shake harder" does not paradoxically make the second shake vanish.
        if (last == 0 || now <= last || (now - last) < forcedReleaseUS) return;
        __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 0);
    }
    if (a < trigger) return;


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
    __sync_lock_test_and_set(&g_gravity_tilt_next_submit_us, 0);

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
            settings_gravity_submit_tilt_async(angle,
                                               effectiveMagnitude,
                                               generation,
                                               mm);
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
            settings_gravity_submit_tilt_async(angle,
                                               effectiveMagnitude,
                                               generation,
                                               mm);
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
    __sync_lock_test_and_set(&g_gravity_tilt_next_submit_us, 0);
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

    old_apply = r'''static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)
{
    if (![d boolForKey:kSettingsGravityLiteEnabled]) return false;
    return gravitylite_apply_in_session(settings_gravitylite_config_from_defaults(d));
}'''
    new_apply = r'''static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)
{
    // Gravity Lite is now an armed shake gesture instead of an immediate
    // physics apply. The first double-shake starts physics; the next restores.
    return settings_arm_gravitylite_for_background_start_locked(d, "apply");
}'''
    text = replace_once(text, old_apply, new_apply, "gravity apply semantics")

    # Compile-safety guard: these FastLockX helpers live immediately after the
    # Gravity apply helper in upstream SettingsViewController.m. Never consume
    # them as part of a broad region replacement.
    required_fastlock_helpers = (
        "static double settings_fastlockx_lite_retry_interval(NSUserDefaults *d)",
        "static FastLockXLiteConfig settings_fastlockx_lite_config_from_defaults(NSUserDefaults *d,",
    )
    for helper in required_fastlock_helpers:
        if helper not in text:
            raise RuntimeError(f"compile-safety guard failed: missing {helper}")

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
    new_help = '''Shake gesture: while Gravity Lite is enabled and Cyanide remains alive in the background, two deliberate hard shakes start page-local physics on the real SpringBoard icon views. Icons are never reparented into a window overlay. When paging begins, the current page's real icons absorb smoothly back to their saved grid positions; after the destination page remains stable briefly, true-icon physics starts there while Dock physics is preserved. Two more hard shakes perform the longer absorb-to-grid restore and stop physics. Activator/Home-button hooks, drag gestures, and preference-daemon notifications are still not included.'''
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
    print("[GRAVITY-SHAKE] v5 page-local TRUE ICON + absorb-on-paging + animated-restore patched SettingsViewController.m + gravitylite core")
    print("[GRAVITY-SHAKE] keep-alive audio is reused from Cyanide's existing DSKeepAlive; no new audio session is added")


if __name__ == "__main__":
    main()
