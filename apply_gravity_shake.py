#!/usr/bin/env python3
from pathlib import Path
import sys


def fail(msg: str) -> None:
    raise SystemExit(f"[GRAVITY-SHAKE] {msg}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    i = text.find(old)
    if i < 0:
        fail(f"basic anchor missing: {label}")
    return text[:i] + new + text[i + len(old):]


def replace_region(text: str, start: str, end: str, replacement: str, label: str) -> str:
    a = text.find(start)
    if a < 0:
        fail(f"basic start anchor missing: {label}")
    b = text.find(end, a)
    if b < 0:
        fail(f"basic end anchor missing: {label}")
    return text[:a] + replacement.rstrip() + "\n\n" + text[b:]


MOTION_BLOCK = r'''static BOOL settings_gravity_motion_can_remote_call(uint64_t generation,
                                                    CMMotionManager *manager)
{
    return manager &&
           manager == g_gravity_motion_manager &&
           generation == g_gravity_motion_generation &&
           g_gravity_motion_stop_requested == 0 &&
           g_springboard_rc_ready != 0 &&
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
            (void)settings_refresh_screen_awake_state("gravity double-shake");
            (void)settings_refresh_screen_lock_state("gravity double-shake");
            if (!settings_screen_awake_cached() || settings_screen_locked_cached()) {
                return;
            }

            if (!turningOn) {
                __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
            }

            @synchronized (settings_rc_lock()) {
                if (settings_cleanup_in_progress() ||
                    !g_springboard_rc_ready ||
                    ![d boolForKey:kSettingsGravityLiteEnabled]) {
                    return;
                }

                if (turningOn) {
                    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
                    ok = gravitylite_apply_in_session(config);
                    if (ok) {
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 1);
                    }
                } else {
                    ok = gravitylite_stop_in_session();
                    if (!ok) {
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 1);
                    }
                }
            }

            if (ok) {
                settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);
                log_user(turningOn
                    ? "[GRAVITY] Double shake: physics ON. Shake twice again to restore.\n"
                    : "[GRAVITY] Double shake: icons restored. Shake twice again for physics.\n");
                settings_notify_package_queue_changed_async();
            } else {
                log_user(turningOn
                    ? "[WARN] Gravity Lite heard the double shake, but physics failed to start.\n"
                    : "[WARN] Gravity Lite heard the double shake, but restore failed.\n");
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
    const double trigger = gravityRemoved ? 0.55 : 1.65;
    const double release = gravityRemoved ? 0.30 : 1.20;
    const uint64_t minPulseGapUS = 180000ULL;
    const uint64_t forcedReleaseUS = 300000ULL;
    const uint64_t doubleShakeWindowUS = 2500000ULL;
    const uint64_t postToggleCooldownUS = 900000ULL;

    uint64_t now = settings_gravity_sensor_now_us();
    if (now == 0 || now < g_gravity_shake_cooldown_until_us) return;

    if (a <= release) {
        __sync_lock_test_and_set(&g_gravity_shake_waiting_for_release, 0);
        return;
    }

    uint64_t last = g_gravity_shake_last_pulse_us;
    if (g_gravity_shake_waiting_for_release != 0) {
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
        printf("[GRAVITY] shake 1/2 detected acceleration=%.2fg\n", a);
        return;
    }

    __sync_lock_test_and_set(&g_gravity_shake_first_pulse_us, 0);
    __sync_lock_test_and_set(&g_gravity_shake_cooldown_until_us,
                             now + postToggleCooldownUS);
    printf("[GRAVITY] shake 2/2 detected acceleration=%.2fg\n", a);
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

            if (g_gravitylite_physics_active == 0) return;

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

    printf("[GRAVITY] Reliable double-shake detector armed; original Gravity core preserved.\n");
}

static void settings_stop_gravity_motion(void)
{
    __sync_lock_test_and_set(&g_gravity_motion_stop_requested, 1);
    __sync_add_and_fetch(&g_gravity_motion_generation, 1);
    settings_gravity_reset_shake_detector();

    CMMotionManager *mm = g_gravity_motion_manager;
    if (!mm) return;

    g_gravity_motion_manager = nil;
    [mm stopDeviceMotionUpdates];
    [mm stopAccelerometerUpdates];
    printf("[GRAVITY] Accelerometer stopped.\n");
}'''


ARM_BLOCK = r'''static bool settings_arm_gravitylite_for_background_start_locked(NSUserDefaults *d,
                                                                 const char *reason)
{
    if (![d boolForKey:kSettingsGravityLiteEnabled]) return false;

    bool stopped = gravitylite_stop_in_session();
    __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
    __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);

    ds_keepalive_apply_enabled(YES);

    GravityLiteConfig config = settings_gravitylite_config_from_defaults(d);
    settings_start_gravity_motion(config.magnitude, config.explosionForce);
    settings_mark_tweak_applied(kSettingsGravityLiteEnabled, YES);

    printf("[SETTINGS] Gravity Lite double-shake armed%s%s stop=%d\n",
           reason ? ": " : "", reason ?: "", stopped);
    return true;
}'''


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    settings = root / "Cyanide" / "SettingsViewController.m"
    if not settings.exists():
        fail(f"missing {settings}")

    text = settings.read_text(encoding="utf-8")

    if "Reliable double-shake detector armed; original Gravity core preserved." in text:
        print("[GRAVITY-SHAKE] already applied")
        return

    old_globals = '''static volatile int g_gravity_motion_stop_requested = 1;
static volatile uint64_t g_gravity_motion_generation = 0;
static CMMotionManager *g_gravity_motion_manager = nil;'''
    new_globals = '''static volatile int g_gravity_motion_stop_requested = 1;
static volatile uint64_t g_gravity_motion_generation = 0;
static CMMotionManager *g_gravity_motion_manager = nil;
static volatile int g_gravitylite_physics_active = 0;
static volatile int g_gravity_shake_toggle_running = 0;
static volatile uint64_t g_gravity_shake_first_pulse_us = 0;
static volatile uint64_t g_gravity_shake_last_pulse_us = 0;
static volatile uint64_t g_gravity_shake_cooldown_until_us = 0;
static volatile int g_gravity_shake_waiting_for_release = 0;'''
    text = replace_once(text, old_globals, new_globals, "Gravity shake state")

    old_decl = '''static void settings_mark_tweak_applied(NSString *key, BOOL applied);
static void settings_notify_package_queue_changed_async(void);'''
    new_decl = '''static void settings_mark_tweak_applied(NSString *key, BOOL applied);
static void settings_notify_package_queue_changed_async(void);
static GravityLiteConfig settings_gravitylite_config_from_defaults(NSUserDefaults *d);'''
    text = replace_once(text, old_decl, new_decl, "Gravity forward declaration")

    text = replace_region(
        text,
        "static BOOL settings_gravity_motion_can_remote_call(uint64_t generation,",
        "typedef void (*SettingsTweakRequestStopFunc)(void);",
        MOTION_BLOCK,
        "Gravity sensor block",
    )

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
    text = replace_once(text, old_request, new_request, "Gravity stop request")

    old_apply = r'''static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)
{
    if (![d boolForKey:kSettingsGravityLiteEnabled]) return false;
    return gravitylite_apply_in_session(settings_gravitylite_config_from_defaults(d));
}'''
    new_apply = r'''static bool settings_apply_gravitylite_from_defaults_locked(NSUserDefaults *d)
{
    return settings_arm_gravitylite_for_background_start_locked(d, "apply");
}'''
    text = replace_once(text, old_apply, new_apply, "Gravity apply semantics")

    arm_start = "static bool settings_arm_gravitylite_for_background_start_locked(NSUserDefaults *d,\n                                                                 const char *reason)\n{"
    arm_end = "static BOOL settings_gravitylite_start_window_ready(const char *reason)"
    text = replace_region(text, arm_start, arm_end, ARM_BLOCK, "Gravity arm helper")

    run_start = '''                    if (runGravityLite) {
                        settings_progress(&step, total, "Starting Gravity Lite icon physics");'''
    run_end = '''                    } else if (!gravityLiteEnabled) {'''
    run_block = r'''                    if (runGravityLite) {
                        settings_progress(&step, total, "Arming Gravity Lite double-shake");
                        log_user("[GRAVITY] Arming reliable double-shake trigger...\n");

                        __sync_lock_test_and_set(&g_gravitylite_background_armed, 0);
                        __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                        settings_stop_gravity_motion();
                        (void)gravitylite_stop_in_session();

                        ds_keepalive_apply_enabled(YES);
                        GravityLiteConfig glConfig = settings_gravitylite_config_from_defaults(d);
                        settings_start_gravity_motion(glConfig.magnitude,
                                                      glConfig.explosionForce);

                        settings_mark_tweak_applied(kSettingsGravityLiteEnabled,
                                                    [d boolForKey:kSettingsGravityLiteEnabled]);
                        log_user("[OK] Gravity Lite armed. Go to the Home Screen and shake twice to start physics; shake twice again to restore.\n");
                        cyanide_upload_log_milestone(@"gravity-lite-shake-armed");
'''
    a = text.find(run_start)
    if a < 0:
        fail("basic anchor missing: Gravity Run block")
    b = text.find(run_end, a)
    if b < 0:
        fail("basic end anchor missing: Gravity Run block")
    text = text[:a] + run_block.rstrip() + "\n" + text[b:]

    old_lock = '''                settings_stop_gravity_motion();
                gravitylite_forget_remote_state();'''
    new_lock = '''                __sync_lock_test_and_set(&g_gravitylite_physics_active, 0);
                settings_stop_gravity_motion();
                gravitylite_forget_remote_state();'''
    if old_lock in text:
        text = text.replace(old_lock, new_lock, 1)

    old_help = "Not included in this core port: Activator/Home-button hooks, drag gestures, automatic shake effects, and preference-daemon notifications."
    new_help = "After Apply, return to the Home Screen and shake the phone twice to start Gravity physics. Shake twice again to restore the icons. Cyanide automatically keeps its existing background runtime alive while the gesture is armed. Activator/Home-button hooks, drag gestures, and preference-daemon notifications are not included."
    text = text.replace(old_help, new_help)

    settings.write_text(text, encoding="utf-8")

    print("[GRAVITY-SHAKE] Applied Settings-only double-shake patch.")
    print("[GRAVITY-SHAKE] Original Cyanide gravitylite.m/h were intentionally left untouched.")
    print("[GRAVITY-SHAKE] Existing DSKeepAlive is started automatically while armed.")


if __name__ == "__main__":
    main()
