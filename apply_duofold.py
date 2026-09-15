#!/usr/bin/env python3
from pathlib import Path
import sys

DUOFOLD_H = '//\n//  duofold.h\n//  Cyanide\n//\n//  Motion-driven "frosted glass fold" effect adapted for Cyanide\'s\n//  RemoteCall-only SpringBoard architecture.\n//\n\n#ifndef duofold_h\n#define duofold_h\n\n#import <Foundation/Foundation.h>\n#import <stdbool.h>\n\ntypedef struct {\n    double eyeDistanceMillimeters;\n    double pointsPerMillimeter;\n    double blurSpread;\n    double darkening;\n} DuoFoldConfig;\n\ntypedef void (^DuoFoldMotionHandler)(double angleRadians);\n\nDuoFoldConfig duofold_default_config(void);\n\n// App-side Core Motion feed. The handler is throttled to ~30 Hz and runs on a\n// background queue; callers should serialize SpringBoard RemoteCall work.\nvoid duofold_motion_start(DuoFoldMotionHandler handler);\nvoid duofold_motion_stop(void);\nvoid duofold_motion_recalibrate(void);\nbool duofold_motion_running(void);\n\n// SpringBoard-side rendering, called while a RemoteCall session is active.\nbool duofold_update_in_session(double angleRadians, DuoFoldConfig config);\nbool duofold_stop_in_session(void);\nbool duofold_remote_active(void);\nvoid duofold_forget_remote_state(void);\n\n#endif /* duofold_h */\n'
DUOFOLD_M = '//\n//  duofold.m\n//  Cyanide\n//\n//  RemoteCall adaptation of the physical model used by DuoLikeAnimation:\n//  https://github.com/elijah-semyonov/DuoLikeAnimation\n//\n//  The upstream motion model and fold optics are MIT licensed:\n//  Copyright (c) 2026 Elijah Semyonov\n//\n//  Permission is hereby granted, free of charge, to any person obtaining a copy\n//  of this software and associated documentation files (the "Software"), to deal\n//  in the Software without restriction, including without limitation the rights\n//  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n//  copies of the Software, and to permit persons to whom the Software is\n//  furnished to do so, subject to the following conditions:\n//\n//  The above copyright notice and this permission notice shall be included in\n//  all copies or substantial portions of the Software.\n//\n//  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n//  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n//  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.\n//\n\n#import "duofold.h"\n#import "remote_objc.h"\n\n#import <CoreMotion/CoreMotion.h>\n#import <QuartzCore/QuartzCore.h>\n#import <UIKit/UIKit.h>\n#import <math.h>\n#import <stdio.h>\n#import <string.h>\n\ntypedef struct { double x, y; } DFPoint;\ntypedef struct { double x, y, w, h; } DFRect;\ntypedef struct {\n    double m11, m12, m13, m14;\n    double m21, m22, m23, m24;\n    double m31, m32, m33, m34;\n    double m41, m42, m43, m44;\n} DFTransform3D;\n\ntypedef struct { double x, y, z; } DFVec3;\ntypedef struct { double m[3][3]; } DFMat3;\n\nstatic CMMotionManager *sMotionManager;\nstatic NSOperationQueue *sMotionQueue;\nstatic DuoFoldMotionHandler sMotionHandler;\nstatic dispatch_source_t sOrientationTimer;\nstatic volatile int sMotionSubmitBusy = 0;\nstatic volatile int sMotionRunning = 0;\nstatic volatile int sOrientation = UIInterfaceOrientationPortrait;\nstatic BOOL sHasReference = NO;\nstatic DFMat3 sReference;\nstatic int sRowsAreDeviceAxes = -1;\nstatic double sMotionTilt = 0.0;\nstatic CFTimeInterval sLastSubmitTime = 0.0;\n\nstatic uint64_t sWindow = 0;\nstatic uint64_t sRootVC = 0;\nstatic uint64_t sContainer = 0;\nstatic uint64_t sBlurView = 0;\nstatic uint64_t sDarkView = 0;\nstatic uint64_t sBlurMask = 0;\nstatic uint64_t sDarkMask = 0;\nstatic uint64_t sSceneVC = 0;\nstatic int sHingeRight = -1;\nstatic double sWidth = 0.0;\nstatic double sHeight = 0.0;\n\nstatic const double kDuoFoldStartAngle = 3.0 * M_PI / 180.0;\nstatic const double kDuoFoldStopAngle  = 1.0 * M_PI / 180.0;\nstatic const double kDuoFoldMaxAngle   = 80.0 * M_PI / 180.0;\n\nDuoFoldConfig duofold_default_config(void)\n{\n    DuoFoldConfig c = {\n        .eyeDistanceMillimeters = 320.0,\n        .pointsPerMillimeter = 6.0,\n        .blurSpread = 0.12,\n        .darkening = 0.015,\n    };\n    return c;\n}\n\n#pragma mark - Local motion model\n\nstatic NSObject *df_motion_lock(void)\n{\n    static NSObject *lock;\n    static dispatch_once_t once;\n    dispatch_once(&once, ^{ lock = [NSObject new]; });\n    return lock;\n}\n\nstatic DFVec3 df_vec(double x, double y, double z)\n{\n    return (DFVec3){x, y, z};\n}\n\nstatic double df_dot(DFVec3 a, DFVec3 b)\n{\n    return a.x*b.x + a.y*b.y + a.z*b.z;\n}\n\nstatic DFVec3 df_normalize(DFVec3 v)\n{\n    double n = sqrt(df_dot(v, v));\n    if (n < 1e-9) return df_vec(0, 0, 0);\n    return df_vec(v.x/n, v.y/n, v.z/n);\n}\n\nstatic DFMat3 df_transpose(DFMat3 a)\n{\n    DFMat3 r = {0};\n    for (int i = 0; i < 3; i++)\n        for (int j = 0; j < 3; j++)\n            r.m[i][j] = a.m[j][i];\n    return r;\n}\n\nstatic DFMat3 df_mul(DFMat3 a, DFMat3 b)\n{\n    DFMat3 r = {0};\n    for (int i = 0; i < 3; i++)\n        for (int j = 0; j < 3; j++)\n            for (int k = 0; k < 3; k++)\n                r.m[i][j] += a.m[i][k] * b.m[k][j];\n    return r;\n}\n\nstatic DFVec3 df_mul_vec(DFMat3 a, DFVec3 v)\n{\n    return df_vec(a.m[0][0]*v.x + a.m[0][1]*v.y + a.m[0][2]*v.z,\n                  a.m[1][0]*v.x + a.m[1][1]*v.y + a.m[1][2]*v.z,\n                  a.m[2][0]*v.x + a.m[2][1]*v.y + a.m[2][2]*v.z);\n}\n\nstatic DFVec3 df_col(DFMat3 a, int c)\n{\n    return df_vec(a.m[0][c], a.m[1][c], a.m[2][c]);\n}\n\nstatic DFMat3 df_device_to_reference(CMDeviceMotion *motion)\n{\n    CMRotationMatrix m = motion.attitude.rotationMatrix;\n    DFMat3 asRows = {{\n        {m.m11, m.m12, m.m13},\n        {m.m21, m.m22, m.m23},\n        {m.m31, m.m32, m.m33},\n    }};\n\n    if (sRowsAreDeviceAxes < 0) {\n        DFVec3 gravity = df_normalize(df_vec(motion.gravity.x,\n                                             motion.gravity.y,\n                                             motion.gravity.z));\n        DFVec3 down = df_vec(0.0, 0.0, -1.0);\n        double rowsScore = df_dot(gravity, df_mul_vec(asRows, down));\n        double columnsScore = df_dot(gravity, df_mul_vec(df_transpose(asRows), down));\n        if (fabs(rowsScore - columnsScore) > 0.2)\n            sRowsAreDeviceAxes = rowsScore > columnsScore ? 1 : 0;\n    }\n\n    return (sRowsAreDeviceAxes < 0 || sRowsAreDeviceAxes == 1)\n        ? df_transpose(asRows)\n        : asRows;\n}\n\nstatic void df_screen_axes(DFVec3 *x, DFVec3 *y)\n{\n    UIInterfaceOrientation o = (UIInterfaceOrientation)__atomic_load_n(&sOrientation, __ATOMIC_RELAXED);\n    switch (o) {\n        case UIInterfaceOrientationLandscapeLeft:\n            *x = df_vec(0, 1, 0);\n            *y = df_vec(-1, 0, 0);\n            break;\n        case UIInterfaceOrientationLandscapeRight:\n            *x = df_vec(0, -1, 0);\n            *y = df_vec(1, 0, 0);\n            break;\n        case UIInterfaceOrientationPortraitUpsideDown:\n            *x = df_vec(-1, 0, 0);\n            *y = df_vec(0, -1, 0);\n            break;\n        default:\n            *x = df_vec(1, 0, 0);\n            *y = df_vec(0, 1, 0);\n            break;\n    }\n}\n\nstatic UIInterfaceOrientation df_interface_orientation_main(void)\n{\n    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {\n        if ([scene isKindOfClass:UIWindowScene.class])\n            return ((UIWindowScene *)scene).interfaceOrientation;\n    }\n    return UIInterfaceOrientationPortrait;\n}\n\nstatic void df_start_orientation_timer(void)\n{\n    dispatch_async(dispatch_get_main_queue(), ^{\n        __atomic_store_n(&sOrientation, (int)df_interface_orientation_main(), __ATOMIC_RELAXED);\n        if (sOrientationTimer) return;\n        dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,\n                                                         dispatch_get_main_queue());\n        sOrientationTimer = timer;\n        dispatch_source_set_timer(timer,\n                                  dispatch_time(DISPATCH_TIME_NOW, 0),\n                                  (uint64_t)(0.5 * NSEC_PER_SEC),\n                                  (uint64_t)(0.05 * NSEC_PER_SEC));\n        dispatch_source_set_event_handler(timer, ^{\n            if (!sMotionRunning) return;\n            __atomic_store_n(&sOrientation, (int)df_interface_orientation_main(), __ATOMIC_RELAXED);\n        });\n        dispatch_resume(timer);\n    });\n}\n\nstatic void df_stop_orientation_timer(void)\n{\n    dispatch_async(dispatch_get_main_queue(), ^{\n        if (!sOrientationTimer) return;\n        dispatch_source_cancel(sOrientationTimer);\n        sOrientationTimer = nil;\n    });\n}\n\nstatic void df_process_motion(CMDeviceMotion *motion, CMMotionManager *manager)\n{\n    DuoFoldMotionHandler submit = nil;\n    double angle = 0.0;\n\n    @synchronized (df_motion_lock()) {\n        if (!sMotionRunning || manager != sMotionManager || !motion) return;\n\n        DFMat3 current = df_device_to_reference(motion);\n        if (!sHasReference) {\n            sReference = current;\n            sHasReference = YES;\n            sMotionTilt = 0.0;\n            return;\n        }\n\n        DFMat3 relative = df_mul(df_transpose(sReference), current);\n        DFVec3 normal = df_col(relative, 2);\n        DFVec3 screenX, screenY;\n        df_screen_axes(&screenX, &screenY);\n\n        double measured = atan2(df_dot(normal, screenX), normal.z);\n        DFVec3 rate = df_vec(motion.rotationRate.x,\n                             motion.rotationRate.y,\n                             motion.rotationRate.z);\n        double predicted = measured + df_dot(rate, screenY) * 0.04;\n        sMotionTilt += (predicted - sMotionTilt) * 0.7;\n\n        CFTimeInterval now = CACurrentMediaTime();\n        if (now - sLastSubmitTime < (1.0 / 30.0)) return;\n        sLastSubmitTime = now;\n        angle = sMotionTilt;\n        submit = [sMotionHandler copy];\n    }\n\n    if (!submit) return;\n    if (__sync_lock_test_and_set(&sMotionSubmitBusy, 1)) return;\n\n    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{\n        @autoreleasepool {\n            submit(angle);\n        }\n        __sync_lock_release(&sMotionSubmitBusy);\n    });\n}\n\nvoid duofold_motion_start(DuoFoldMotionHandler handler)\n{\n    if (!handler) return;\n\n    @synchronized (df_motion_lock()) {\n        if (sMotionManager) {\n            [sMotionManager stopDeviceMotionUpdates];\n            sMotionManager = nil;\n        }\n\n        sMotionHandler = [handler copy];\n        sHasReference = NO;\n        sRowsAreDeviceAxes = -1;\n        sMotionTilt = 0.0;\n        sLastSubmitTime = 0.0;\n        __sync_lock_test_and_set(&sMotionSubmitBusy, 0);\n\n        CMMotionManager *mm = [[CMMotionManager alloc] init];\n        if (!mm.deviceMotionAvailable) {\n            printf("[DUOFOLD] Device motion unavailable.\\n");\n            sMotionHandler = nil;\n            return;\n        }\n\n        NSOperationQueue *q = [[NSOperationQueue alloc] init];\n        q.maxConcurrentOperationCount = 1;\n        q.qualityOfService = NSQualityOfServiceUserInteractive;\n\n        sMotionManager = mm;\n        sMotionQueue = q;\n        sMotionRunning = 1;\n        mm.deviceMotionUpdateInterval = 1.0 / 120.0;\n\n        [mm startDeviceMotionUpdatesUsingReferenceFrame:CMAttitudeReferenceFrameXArbitraryZVertical\n                                               toQueue:q\n                                           withHandler:^(CMDeviceMotion *motion, NSError *error) {\n            if (!error && motion) df_process_motion(motion, mm);\n        }];\n    }\n\n    df_start_orientation_timer();\n    printf("[DUOFOLD] Motion feed active at 120 Hz; RemoteCall submissions capped at 30 Hz.\\n");\n}\n\nvoid duofold_motion_stop(void)\n{\n    @synchronized (df_motion_lock()) {\n        sMotionRunning = 0;\n        [sMotionManager stopDeviceMotionUpdates];\n        sMotionManager = nil;\n        sMotionQueue = nil;\n        sMotionHandler = nil;\n        sHasReference = NO;\n        sRowsAreDeviceAxes = -1;\n        sMotionTilt = 0.0;\n        sLastSubmitTime = 0.0;\n    }\n    df_stop_orientation_timer();\n    printf("[DUOFOLD] Motion feed stopped.\\n");\n}\n\nvoid duofold_motion_recalibrate(void)\n{\n    @synchronized (df_motion_lock()) {\n        sHasReference = NO;\n        sRowsAreDeviceAxes = -1;\n        sMotionTilt = 0.0;\n    }\n    printf("[DUOFOLD] Zero pose will be captured from the next motion sample.\\n");\n}\n\nbool duofold_motion_running(void)\n{\n    return sMotionRunning != 0;\n}\n\n#pragma mark - Remote UIKit helpers\n\nstatic void df_release(uint64_t obj)\n{\n    if (r_is_objc_ptr(obj)) r_msg2_main(obj, "release", 0, 0, 0, 0);\n}\n\nstatic void df_retain(uint64_t obj)\n{\n    if (r_is_objc_ptr(obj)) r_msg2_main(obj, "retain", 0, 0, 0, 0);\n}\n\nstatic void df_set_double(uint64_t obj, const char *sel, double v)\n{\n    if (!r_is_objc_ptr(obj) || !r_responds_main(obj, sel)) return;\n    r_msg2_main_raw(obj, sel, &v, sizeof(v), NULL, 0, NULL, 0, NULL, 0);\n}\n\nstatic void df_set_rect(uint64_t obj, const char *sel, DFRect r)\n{\n    if (!r_is_objc_ptr(obj) || !r_responds_main(obj, sel)) return;\n    r_msg2_main_raw(obj, sel, &r, sizeof(r), NULL, 0, NULL, 0, NULL, 0);\n}\n\nstatic bool df_get_rect(uint64_t obj, const char *sel, DFRect *out)\n{\n    if (!r_is_objc_ptr(obj) || !out || !r_responds_main(obj, sel)) return false;\n    memset(out, 0, sizeof(*out));\n    return r_msg2_main_struct_ret(obj, sel, out, sizeof(*out),\n                                  NULL, 0, NULL, 0, NULL, 0, NULL, 0);\n}\n\nstatic void df_set_point(uint64_t obj, const char *sel, DFPoint p)\n{\n    if (!r_is_objc_ptr(obj) || !r_responds_main(obj, sel)) return;\n    r_msg2_main_raw(obj, sel, &p, sizeof(p), NULL, 0, NULL, 0, NULL, 0);\n}\n\nstatic uint64_t df_new(const char *className)\n{\n    uint64_t cls = r_class(className);\n    return r_is_objc_ptr(cls) ? r_msg2_main(cls, "new", 0, 0, 0, 0) : 0;\n}\n\nstatic uint64_t df_color(double white, double alpha)\n{\n    uint64_t UIColor = r_class("UIColor");\n    if (!r_is_objc_ptr(UIColor)) return 0;\n    return r_msg2_main_raw(UIColor, "colorWithWhite:alpha:",\n                           &white, sizeof(white),\n                           &alpha, sizeof(alpha),\n                           NULL, 0, NULL, 0);\n}\n\nstatic uint64_t df_gradient_mask(DFRect frame, bool hingeRight)\n{\n    uint64_t cls = r_class("CAGradientLayer");\n    uint64_t mask = r_is_objc_ptr(cls) ? r_msg2_main(cls, "layer", 0, 0, 0, 0) : 0;\n    if (!r_is_objc_ptr(mask)) return 0;\n    df_retain(mask);\n    df_set_rect(mask, "setFrame:", frame);\n\n    uint64_t colors = df_new("NSMutableArray");\n    if (r_is_objc_ptr(colors)) {\n        static const double alphas[] = {0.0, 0.08, 0.34, 1.0};\n        for (int i = 0; i < 4; i++) {\n            uint64_t color = df_color(0.0, alphas[i]);\n            uint64_t cg = r_is_objc_ptr(color) && r_responds_main(color, "CGColor")\n                ? r_msg2_main(color, "CGColor", 0, 0, 0, 0) : 0;\n            if (cg) r_msg2_main(colors, "addObject:", cg, 0, 0, 0);\n        }\n        r_msg2_main(mask, "setColors:", colors, 0, 0, 0);\n        df_release(colors);\n    }\n\n    DFPoint start = hingeRight ? (DFPoint){1.0, 0.5} : (DFPoint){0.0, 0.5};\n    DFPoint end   = hingeRight ? (DFPoint){0.0, 0.5} : (DFPoint){1.0, 0.5};\n    df_set_point(mask, "setStartPoint:", start);\n    df_set_point(mask, "setEndPoint:", end);\n    return mask;\n}\n\nstatic void df_set_gradient_direction(uint64_t mask, bool hingeRight)\n{\n    if (!r_is_objc_ptr(mask)) return;\n    DFPoint start = hingeRight ? (DFPoint){1.0, 0.5} : (DFPoint){0.0, 0.5};\n    DFPoint end   = hingeRight ? (DFPoint){0.0, 0.5} : (DFPoint){1.0, 0.5};\n    df_set_point(mask, "setStartPoint:", start);\n    df_set_point(mask, "setEndPoint:", end);\n}\n\nstatic bool df_screen_bounds(DFRect *out)\n{\n    uint64_t UIScreen = r_class("UIScreen");\n    uint64_t screen = r_is_objc_ptr(UIScreen)\n        ? r_msg2_main(UIScreen, "mainScreen", 0, 0, 0, 0) : 0;\n    return df_get_rect(screen, "bounds", out);\n}\n\nstatic bool df_is_self_bundle(uint64_t handle)\n{\n    if (!r_is_objc_ptr(handle) || !r_responds_main(handle, "application")) return false;\n    uint64_t app = r_msg2_main(handle, "application", 0, 0, 0, 0);\n    uint64_t bid = r_is_objc_ptr(app) && r_responds_main(app, "bundleIdentifier")\n        ? r_msg2_main(app, "bundleIdentifier", 0, 0, 0, 0) : 0;\n    char buf[160] = {0};\n    if (!r_read_nsstring(bid, buf, sizeof(buf))) return false;\n    return strcmp(buf, "com.zeroxjf.ios-cyanide1") == 0;\n}\n\nstatic uint64_t df_current_scene_handle(void)\n{\n    uint64_t UIApplication = r_class("UIApplication");\n    uint64_t app = r_is_objc_ptr(UIApplication)\n        ? r_msg2_main(UIApplication, "sharedApplication", 0, 0, 0, 0) : 0;\n    if (!r_is_objc_ptr(app) || !r_responds_main(app, "windowSceneManager")) return 0;\n\n    uint64_t wsm = r_msg2_main(app, "windowSceneManager", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(wsm)) return 0;\n\n    uint64_t windowScene = 0;\n    if (r_responds_main(wsm, "activeDisplayWindowScene"))\n        windowScene = r_msg2_main(wsm, "activeDisplayWindowScene", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(windowScene) && r_responds_main(wsm, "embeddedDisplayWindowScene"))\n        windowScene = r_msg2_main(wsm, "embeddedDisplayWindowScene", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(windowScene) || !r_responds_main(windowScene, "switcherController")) return 0;\n\n    uint64_t switcher = r_msg2_main(windowScene, "switcherController", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(switcher) ||\n        !r_responds_main(switcher, "layoutStateApplicationSceneHandles")) return 0;\n\n    uint64_t set = r_msg2_main(switcher, "layoutStateApplicationSceneHandles", 0, 0, 0, 0);\n    uint64_t arr = r_is_objc_ptr(set) && r_responds_main(set, "allObjects")\n        ? r_msg2_main(set, "allObjects", 0, 0, 0, 0) : 0;\n    uint64_t count = r_is_objc_ptr(arr) && r_responds_main(arr, "count")\n        ? r_msg2_main(arr, "count", 0, 0, 0, 0) : 0;\n    if (count == 0 || count > 32) return 0;\n\n    for (uint64_t i = 0; i < count; i++) {\n        uint64_t handle = r_msg2_main(arr, "objectAtIndex:", i, 0, 0, 0);\n        if (!r_is_objc_ptr(handle) || df_is_self_bundle(handle)) continue;\n        if (r_responds_main(handle, "newSceneViewController")) return handle;\n    }\n    return 0;\n}\n\nstatic uint64_t df_make_foreground_source(DFRect frame)\n{\n    sSceneVC = 0;\n\n    uint64_t handle = df_current_scene_handle();\n    if (r_is_objc_ptr(handle)) {\n        uint64_t vc = r_msg2_main(handle, "newSceneViewController", 0, 0, 0, 0);\n        uint64_t view = r_is_objc_ptr(vc) && r_responds_main(vc, "view")\n            ? r_msg2_main(vc, "view", 0, 0, 0, 0) : 0;\n        if (r_is_objc_ptr(vc) && r_is_objc_ptr(view)) {\n            sSceneVC = vc; // newSceneViewController is +1\n            df_set_rect(view, "setFrame:", frame);\n            r_msg2_main(view, "setUserInteractionEnabled:", 0, 0, 0, 0);\n            if (r_responds_main(view, "layoutIfNeeded"))\n                r_msg2_main(view, "layoutIfNeeded", 0, 0, 0, 0);\n            printf("[DUOFOLD] Using live foreground scene handle 0x%llx.\\n",\n                   (unsigned long long)handle);\n            return view;\n        }\n        if (r_is_objc_ptr(vc)) df_release(vc);\n    }\n\n    uint64_t UIApplication = r_class("UIApplication");\n    uint64_t app = r_is_objc_ptr(UIApplication)\n        ? r_msg2_main(UIApplication, "sharedApplication", 0, 0, 0, 0) : 0;\n    uint64_t window = r_is_objc_ptr(app) && r_responds_main(app, "keyWindow")\n        ? r_msg2_main(app, "keyWindow", 0, 0, 0, 0) : 0;\n\n    if (!r_is_objc_ptr(window) && r_is_objc_ptr(app) && r_responds_main(app, "windows")) {\n        uint64_t windows = r_msg2_main(app, "windows", 0, 0, 0, 0);\n        uint64_t count = r_is_objc_ptr(windows) ? r_msg2_main(windows, "count", 0, 0, 0, 0) : 0;\n        if (count > 0) window = r_msg2_main(windows, "objectAtIndex:", 0, 0, 0, 0);\n    }\n\n    uint64_t snapshot = r_is_objc_ptr(window) &&\n                        r_responds_main(window, "snapshotViewAfterScreenUpdates:")\n        ? r_msg2_main(window, "snapshotViewAfterScreenUpdates:", 0, 0, 0, 0)\n        : 0;\n    if (r_is_objc_ptr(snapshot)) {\n        df_set_rect(snapshot, "setFrame:", frame);\n        r_msg2_main(snapshot, "setUserInteractionEnabled:", 0, 0, 0, 0);\n        printf("[DUOFOLD] Using SpringBoard window snapshot fallback.\\n");\n        return snapshot;\n    }\n\n    return 0;\n}\n\nstatic void df_apply_hinge(bool hingeRight)\n{\n    if (!r_is_objc_ptr(sContainer)) return;\n\n    uint64_t layer = r_msg2_main(sContainer, "layer", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(layer)) return;\n\n    DFTransform3D ident = {0};\n    ident.m11 = ident.m22 = ident.m33 = ident.m44 = 1.0;\n    if (r_responds_main(layer, "setTransform:"))\n        r_msg2_main_raw(layer, "setTransform:",\n                        &ident, sizeof(ident),\n                        NULL, 0, NULL, 0, NULL, 0);\n\n    DFPoint anchor = hingeRight ? (DFPoint){1.0, 0.5} : (DFPoint){0.0, 0.5};\n    df_set_point(layer, "setAnchorPoint:", anchor);\n    df_set_rect(sContainer, "setFrame:", (DFRect){0, 0, sWidth, sHeight});\n    df_set_gradient_direction(sBlurMask, hingeRight);\n    df_set_gradient_direction(sDarkMask, hingeRight);\n    sHingeRight = hingeRight ? 1 : 0;\n}\n\nstatic bool df_build_overlay(bool hingeRight)\n{\n    DFRect bounds = {0};\n    if (!df_screen_bounds(&bounds) || bounds.w <= 1.0 || bounds.h <= 1.0) {\n        printf("[DUOFOLD] Could not resolve screen bounds.\\n");\n        return false;\n    }\n\n    uint64_t source = df_make_foreground_source(bounds);\n    if (!r_is_objc_ptr(source)) {\n        printf("[DUOFOLD] No live scene or SpringBoard snapshot available.\\n");\n        return false;\n    }\n\n    uint64_t window = df_new("UIWindow");\n    uint64_t rootVC = df_new("UIViewController");\n    uint64_t container = df_new("UIView");\n    if (!r_is_objc_ptr(window) || !r_is_objc_ptr(rootVC) || !r_is_objc_ptr(container)) {\n        if (window) df_release(window);\n        if (rootVC) df_release(rootVC);\n        if (container) df_release(container);\n        if (sSceneVC) { df_release(sSceneVC); sSceneVC = 0; }\n        return false;\n    }\n\n    sWindow = window;\n    sRootVC = rootVC;\n    sContainer = container;\n    sWidth = bounds.w;\n    sHeight = bounds.h;\n\n    df_set_rect(window, "setFrame:", bounds);\n    df_set_rect(container, "setFrame:", bounds);\n    r_msg2_main(window, "setUserInteractionEnabled:", 0, 0, 0, 0);\n    r_msg2_main(container, "setUserInteractionEnabled:", 0, 0, 0, 0);\n    r_msg2_main(container, "setClipsToBounds:", 0, 0, 0, 0);\n\n    uint64_t black = r_msg2_main(r_class("UIColor"), "blackColor", 0, 0, 0, 0);\n    if (r_is_objc_ptr(black)) {\n        r_msg2_main(window, "setBackgroundColor:", black, 0, 0, 0);\n        uint64_t rootView = r_msg2_main(rootVC, "view", 0, 0, 0, 0);\n        if (r_is_objc_ptr(rootView))\n            r_msg2_main(rootView, "setBackgroundColor:", black, 0, 0, 0);\n    }\n\n    r_msg2_main(window, "setRootViewController:", rootVC, 0, 0, 0);\n    df_set_double(window, "setWindowLevel:", 999000.0);\n\n    uint64_t rootView = r_msg2_main(rootVC, "view", 0, 0, 0, 0);\n    df_set_rect(rootView, "setFrame:", bounds);\n    r_msg2_main(rootView, "addSubview:", container, 0, 0, 0);\n\n    if (r_is_objc_ptr(sSceneVC) && r_responds_main(rootVC, "addChildViewController:"))\n        r_msg2_main(rootVC, "addChildViewController:", sSceneVC, 0, 0, 0);\n\n    r_msg2_main(container, "addSubview:", source, 0, 0, 0);\n\n    if (r_is_objc_ptr(sSceneVC) && r_responds_main(sSceneVC, "didMoveToParentViewController:"))\n        r_msg2_main(sSceneVC, "didMoveToParentViewController:", rootVC, 0, 0, 0);\n\n    uint64_t UIBlurEffect = r_class("UIBlurEffect");\n    uint64_t effect = r_is_objc_ptr(UIBlurEffect)\n        ? r_msg2_main(UIBlurEffect, "effectWithStyle:", 4 /* regular */, 0, 0, 0)\n        : 0;\n    uint64_t blurAlloc = r_msg2_main(r_class("UIVisualEffectView"), "alloc", 0, 0, 0, 0);\n    uint64_t blur = r_is_objc_ptr(blurAlloc) && r_is_objc_ptr(effect)\n        ? r_msg2_main(blurAlloc, "initWithEffect:", effect, 0, 0, 0)\n        : 0;\n    if (r_is_objc_ptr(blur)) {\n        sBlurView = blur;\n        df_set_rect(blur, "setFrame:", bounds);\n        r_msg2_main(blur, "setUserInteractionEnabled:", 0, 0, 0, 0);\n        sBlurMask = df_gradient_mask(bounds, hingeRight);\n        uint64_t layer = r_msg2_main(blur, "layer", 0, 0, 0, 0);\n        if (r_is_objc_ptr(layer) && r_is_objc_ptr(sBlurMask))\n            r_msg2_main(layer, "setMask:", sBlurMask, 0, 0, 0);\n        r_msg2_main(container, "addSubview:", blur, 0, 0, 0);\n    } else if (r_is_objc_ptr(blurAlloc)) {\n        df_release(blurAlloc);\n    }\n\n    uint64_t dark = df_new("UIView");\n    if (r_is_objc_ptr(dark)) {\n        sDarkView = dark;\n        df_set_rect(dark, "setFrame:", bounds);\n        r_msg2_main(dark, "setUserInteractionEnabled:", 0, 0, 0, 0);\n        if (r_is_objc_ptr(black))\n            r_msg2_main(dark, "setBackgroundColor:", black, 0, 0, 0);\n        sDarkMask = df_gradient_mask(bounds, hingeRight);\n        uint64_t layer = r_msg2_main(dark, "layer", 0, 0, 0, 0);\n        if (r_is_objc_ptr(layer) && r_is_objc_ptr(sDarkMask))\n            r_msg2_main(layer, "setMask:", sDarkMask, 0, 0, 0);\n        r_msg2_main(container, "addSubview:", dark, 0, 0, 0);\n    }\n\n    df_apply_hinge(hingeRight);\n    df_set_double(sBlurView, "setAlpha:", 0.0);\n    df_set_double(sDarkView, "setAlpha:", 0.0);\n    r_msg2_main(window, "setHidden:", 0, 0, 0, 0);\n\n    printf("[DUOFOLD] SpringBoard fold overlay created (%.0fx%.0f pt).\\n",\n           bounds.w, bounds.h);\n    return true;\n}\n\nstatic DFTransform3D df_inverse_fold_transform(double angle, double eyeDistancePoints)\n{\n    double theta = -angle; // software pre-warp cancels the physical device rotation\n    double c = cos(theta);\n    double s = sin(theta);\n\n    DFTransform3D t = {0};\n    t.m11 = c;\n    t.m13 = -s;\n    t.m22 = 1.0;\n    t.m31 = s;\n    t.m33 = c;\n    t.m34 = -1.0 / fmax(eyeDistancePoints, 200.0);\n    t.m44 = 1.0;\n    return t;\n}\n\nbool duofold_update_in_session(double angleRadians, DuoFoldConfig config)\n{\n    double a = fmax(-kDuoFoldMaxAngle, fmin(kDuoFoldMaxAngle, angleRadians));\n    double absA = fabs(a);\n\n    if (!r_is_objc_ptr(sWindow)) {\n        if (absA < kDuoFoldStartAngle) return true;\n        if (!df_build_overlay(a > 0.0)) return false;\n    } else if (absA < kDuoFoldStopAngle) {\n        return duofold_stop_in_session();\n    }\n\n    bool hingeRight = a > 0.0;\n    if (sHingeRight != (hingeRight ? 1 : 0))\n        df_apply_hinge(hingeRight);\n\n    uint64_t layer = r_msg2_main(sContainer, "layer", 0, 0, 0, 0);\n    if (!r_is_objc_ptr(layer)) return false;\n\n    double eyeDistancePoints = config.eyeDistanceMillimeters * config.pointsPerMillimeter;\n    DFTransform3D transform = df_inverse_fold_transform(a, eyeDistancePoints);\n    r_msg2_main_raw(layer, "setTransform:",\n                    &transform, sizeof(transform),\n                    NULL, 0, NULL, 0, NULL, 0);\n\n    double gap = sWidth * sin(absA);\n    double radius = fmax(0.0, config.blurSpread * gap);\n    double blurAlpha = fmin(1.0, radius / 24.0);\n    double darkAlpha = fmin(0.85, fmax(0.0, config.darkening * radius));\n\n    df_set_double(sBlurView, "setAlpha:", blurAlpha);\n    df_set_double(sDarkView, "setAlpha:", darkAlpha);\n    return true;\n}\n\nbool duofold_stop_in_session(void)\n{\n    if (!r_is_objc_ptr(sWindow)) {\n        duofold_forget_remote_state();\n        return true;\n    }\n\n    r_msg2_main(sWindow, "setHidden:", 1, 0, 0, 0);\n    if (r_is_objc_ptr(sSceneVC) && r_responds_main(sSceneVC, "willMoveToParentViewController:"))\n        r_msg2_main(sSceneVC, "willMoveToParentViewController:", 0, 0, 0, 0);\n    if (r_is_objc_ptr(sContainer))\n        r_msg2_main(sContainer, "removeFromSuperview", 0, 0, 0, 0);\n    r_msg2_main(sWindow, "setRootViewController:", 0, 0, 0, 0);\n\n    df_release(sBlurMask);\n    df_release(sDarkMask);\n    df_release(sBlurView);\n    df_release(sDarkView);\n    df_release(sContainer);\n    df_release(sSceneVC);\n    df_release(sRootVC);\n    df_release(sWindow);\n\n    duofold_forget_remote_state();\n    printf("[DUOFOLD] Fold overlay removed.\\n");\n    return true;\n}\n\nbool duofold_remote_active(void)\n{\n    return sWindow != 0;\n}\n\nvoid duofold_forget_remote_state(void)\n{\n    sWindow = 0;\n    sRootVC = 0;\n    sContainer = 0;\n    sBlurView = 0;\n    sDarkView = 0;\n    sBlurMask = 0;\n    sDarkMask = 0;\n    sSceneVC = 0;\n    sHingeRight = -1;\n    sWidth = 0.0;\n    sHeight = 0.0;\n}\n'

def fail(msg: str) -> None:
    raise SystemExit(f"[DUOFOLD] {msg}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    # Basic detection only: use the first matching anchor.
    pos = text.find(old)
    if pos < 0:
        fail(f"basic anchor missing: {label}")
    return text[:pos] + new + text[pos + len(old):]

def patch_if_missing(text: str, marker: str, old: str, new: str, label: str) -> str:
    if marker in text:
        return text
    return replace_once(text, old, new, label)

def patch_if_missing_any(text: str, marker: str, anchors, prefix: str, label: str) -> str:
    if marker in text:
        return text
    for anchor in anchors:
        pos = text.find(anchor)
        if pos >= 0:
            return text[:pos] + prefix + anchor + text[pos + len(anchor):]
    fail(f"basic anchor missing: {label}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    settings = root / "Cyanide" / "SettingsViewController.m"
    tweaks = root / "Cyanide" / "tweaks"
    if not settings.exists() or not tweaks.is_dir():
        fail("run this from the Cyanide repository root (or pass the repo path)")

    # The Xcode project uses a filesystem-synchronized Cyanide group, so these
    # files are picked up by the target without project.pbxproj edits.
    (tweaks / "duofold.h").write_text(DUOFOLD_H, encoding="utf-8")
    (tweaks / "duofold.m").write_text(DUOFOLD_M, encoding="utf-8")

    text = settings.read_text(encoding="utf-8")
    backup = settings.with_suffix(settings.suffix + ".duofold.bak")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")

    text = patch_if_missing(
        text,
        '#import "tweaks/duofold.h"',
        '#import "tweaks/gravitylite.h"\n',
        '#import "tweaks/gravitylite.h"\n#import "tweaks/duofold.h"\n',
        "Duo Fold import",
    )

    text = patch_if_missing(
        text,
        'kSettingsDuoFoldEnabled = @"DuoFoldEnabled"',
        'NSString * const kSettingsGravityLiteAngularResistancePct = @"GravityLiteAngularResistancePct";\n\nNSString * const kSettingsStageStripEnabled',
        'NSString * const kSettingsGravityLiteAngularResistancePct = @"GravityLiteAngularResistancePct";\n'
        'static NSString * const kSettingsDuoFoldEnabled = @"DuoFoldEnabled";\n\n'
        'NSString * const kSettingsStageStripEnabled',
        "Duo Fold defaults key",
    )

    helper_anchor = '''} SettingsSpringBoardTweakCleanupEntry;

static void settings_request_statbar_stop(void) { g_statbar_live_stop_requested = 1; }'''
    helper_repl = '''} SettingsSpringBoardTweakCleanupEntry;

static volatile int g_duofold_update_warning_logged = 0;

static BOOL settings_duofold_can_render(void)
{
    return [UIApplication sharedApplication].applicationState == UIApplicationStateBackground &&
           g_springboard_rc_ready != 0 &&
           !settings_cleanup_in_progress() &&
           settings_screen_awake_cached() &&
           !settings_screen_locked_cached();
}

static void settings_stop_duofold_motion(void)
{
    duofold_motion_stop();
    __sync_lock_test_and_set(&g_duofold_update_warning_logged, 0);
}

static void settings_start_duofold_motion(void)
{
    NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
    if (![d boolForKey:kSettingsDuoFoldEnabled]) return;

    duofold_motion_recalibrate();
    duofold_motion_start(^(double angleRadians) {
        NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
        if (![defaults boolForKey:kSettingsDuoFoldEnabled]) return;
        if (!g_springboard_rc_ready || settings_cleanup_in_progress()) return;

        if (!settings_duofold_can_render()) {
            if (duofold_remote_active()) {
                @synchronized (settings_rc_lock()) {
                    if (g_springboard_rc_ready && !settings_cleanup_in_progress()) {
                        (void)duofold_stop_in_session();
                    }
                }
            }
            return;
        }

        @synchronized (settings_rc_lock()) {
            if (!settings_duofold_can_render() ||
                ![defaults boolForKey:kSettingsDuoFoldEnabled]) {
                return;
            }

            bool ok = duofold_update_in_session(angleRadians, duofold_default_config());
            if (ok) {
                __sync_lock_test_and_set(&g_duofold_update_warning_logged, 0);
            } else if (__sync_bool_compare_and_swap(&g_duofold_update_warning_logged, 0, 1)) {
                log_user("[WARN] Duo Fold could not build/update its SpringBoard overlay.\n");
            }
        }
    });
}

static void settings_request_duofold_stop(void)
{
    settings_stop_duofold_motion();
}

static BOOL settings_duofold_running(void)
{
    return duofold_motion_running() ? YES : NO;
}

static void settings_request_statbar_stop(void) { g_statbar_live_stop_requested = 1; }'''
    text = patch_if_missing(
        text,
        "static BOOL settings_duofold_can_render(void)",
        helper_anchor,
        helper_repl,
        "Duo Fold motion bridge",
    )

    text = patch_if_missing(
        text,
        "static bool settings_stop_duofold_registered",
        '''static bool settings_stop_gravitylite_registered(BOOL springboardWillDie)
{
    (void)springboardWillDie;
    settings_request_gravitylite_stop();
    return gravitylite_stop_in_session();
}

static bool settings_stop_themer_registered''',
        '''static bool settings_stop_gravitylite_registered(BOOL springboardWillDie)
{
    (void)springboardWillDie;
    settings_request_gravitylite_stop();
    return gravitylite_stop_in_session();
}

static bool settings_stop_duofold_registered(BOOL springboardWillDie)
{
    settings_request_duofold_stop();
    if (springboardWillDie) {
        duofold_forget_remote_state();
        return true;
    }
    return duofold_stop_in_session();
}

static bool settings_stop_themer_registered''',
        "Duo Fold cleanup function",
    )

    text = patch_if_missing(
        text,
        '{ kSettingsDuoFoldEnabled, "Duo Fold"',
        '{ kSettingsGravityLiteEnabled, "Gravity Lite", settings_request_gravitylite_stop, settings_stop_gravitylite_registered, gravitylite_forget_remote_state, NULL, YES, YES },',
        '{ kSettingsGravityLiteEnabled, "Gravity Lite", settings_request_gravitylite_stop, settings_stop_gravitylite_registered, gravitylite_forget_remote_state, NULL, YES, YES },\n'
        '        { kSettingsDuoFoldEnabled, "Duo Fold", settings_request_duofold_stop, settings_stop_duofold_registered, duofold_forget_remote_state, settings_duofold_running, YES, YES },',
        "Duo Fold cleanup registry",
    )

    text = patch_if_missing(
        text,
        'kSettingsDuoFoldEnabled: @NO',
        'kSettingsGravityLiteAngularResistancePct: @0,\n\n        kSettingsStageStripEnabled: @NO,',
        'kSettingsGravityLiteAngularResistancePct: @0,\n'
        '        kSettingsDuoFoldEnabled: @NO,\n\n'
        '        kSettingsStageStripEnabled: @NO,',
        "Duo Fold default value",
    )

    text = patch_if_missing(
        text,
        'BOOL runDuoFold =',
        'BOOL runGravityLite = settings_enabled_tweak_should_run(d, kSettingsGravityLiteEnabled, springBoardPendingOnly);',
        'BOOL runGravityLite = settings_enabled_tweak_should_run(d, kSettingsGravityLiteEnabled, springBoardPendingOnly);\n'
        '            BOOL runDuoFold = settings_enabled_tweak_should_run(d, kSettingsDuoFoldEnabled, springBoardPendingOnly);',
        "Duo Fold run flag",
    )

    text = patch_if_missing(
        text,
        'runGravityLite || runDuoFold || runLayoutExtras',
        'runGravityLite || runLayoutExtras',
        'runGravityLite || runDuoFold || runLayoutExtras',
        "Duo Fold SpringBoard work flag",
    )

    text = patch_if_missing(
        text,
        'if (runDuoFold) total++;',
        'if (runGravityLite) total++;',
        'if (runGravityLite) total++;\n            if (runDuoFold) total++;',
        "Duo Fold progress count",
    )

    text = patch_if_missing(
        text,
        '[enabledTweaks addObject:@"duofold"]',
        'if (runGravityLite) [enabledTweaks addObject:[NSString stringWithFormat:@"gravity(%ld%%)", (long)[d integerForKey:kSettingsGravityLiteMagnitudePct]]];',
        'if (runGravityLite) [enabledTweaks addObject:[NSString stringWithFormat:@"gravity(%ld%%)", (long)[d integerForKey:kSettingsGravityLiteMagnitudePct]]];\n'
        '            if (runDuoFold) [enabledTweaks addObject:@"duofold"];',
        "Duo Fold enabled log",
    )

    gravity_run_anchors = [
        '''                    if (runGravityLite) {
                        settings_progress(&step, total, "Starting Gravity Lite icon physics");''',
        '''                    if (runGravityLite) {
                        settings_progress(&step, total, "Arming Gravity Lite double-shake physics");''',
    ]
    text = patch_if_missing_any(
        text,
        'settings_progress(&step, total, "Starting Duo Fold motion effect")',
        gravity_run_anchors,
        '                    if (runDuoFold) {\n                        settings_progress(&step, total, "Starting Duo Fold motion effect");\n                        (void)duofold_stop_in_session();\n                        settings_start_duofold_motion();\n                        bool ok = duofold_motion_running();\n                        settings_mark_tweak_applied(kSettingsDuoFoldEnabled,\n                                                    ok && [d boolForKey:kSettingsDuoFoldEnabled]);\n                        printf("[SETTINGS] Duo Fold result=%d\\n", ok);\n                        log_user("%s Duo Fold %s.\\n",\n                                 ok ? "[OK]" : "[WARN]",\n                                 ok ? "motion tracking armed — tilt after leaving Cyanide"\n                                    : "could not start Core Motion");\n                        cyanide_upload_log_milestone(ok ? @"duofold-armed" : @"duofold-failed");\n                    }\n\n',
        "Duo Fold run block",
    )

    text = patch_if_missing(
        text,
        '([d boolForKey:kSettingsDuoFoldEnabled] && g_springboard_rc_ready)',
        '''([d boolForKey:kSettingsGravityLiteEnabled] && g_springboard_rc_ready) ||
        themerLiveNeeded ||''',
        '''([d boolForKey:kSettingsGravityLiteEnabled] && g_springboard_rc_ready) ||
        ([d boolForKey:kSettingsDuoFoldEnabled] && g_springboard_rc_ready) ||
        themerLiveNeeded ||''',
        "Duo Fold background live-loop requirement",
    )

    text = patch_if_missing(
        text,
        '@"title": @"Duo Fold animation"',
        '''        @{ @"kind": @"toggle",
           @"key": kSettingsGravityLiteDockEnabled,
           @"title": @"Include Dock" },''',
        '''        @{ @"kind": @"toggle",
           @"key": kSettingsGravityLiteDockEnabled,
           @"title": @"Include Dock" },
        @{ @"kind": @"toggle",
           @"key": kSettingsDuoFoldEnabled,
           @"title": @"Duo Fold animation",
           @"subtitle": @"Uses iPhone motion as a virtual hinge; Keep Alive is recommended." },''',
        "Duo Fold settings row",
    )

    text = patch_if_missing(
        text,
        '@"title": @"Duo Fold",',
        '''        [out addObject:@{@"title": @"Spin resist.", @"value": [NSString stringWithFormat:@"%ld%%", (long)[d integerForKey:kSettingsGravityLiteAngularResistancePct]]}];
    }''',
        '''        [out addObject:@{@"title": @"Spin resist.", @"value": [NSString stringWithFormat:@"%ld%%", (long)[d integerForKey:kSettingsGravityLiteAngularResistancePct]]}];
        [out addObject:@{@"title": @"Duo Fold",    @"value": [d boolForKey:kSettingsDuoFoldEnabled] ? @"On" : @"Off"}];
    }''',
        "Duo Fold settings summary",
    )

    required = (
        '#import "tweaks/duofold.h"',
        'kSettingsDuoFoldEnabled',
        'settings_start_duofold_motion',
        'BOOL runDuoFold =',
        'Starting Duo Fold motion effect',
        '@"title": @"Duo Fold animation"',
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        fail("patch incomplete: " + ", ".join(missing))

    settings.write_text(text, encoding="utf-8")
    print("[DUOFOLD] Applied successfully.")
    print("[DUOFOLD] Added Cyanide/tweaks/duofold.h and duofold.m")
    print("[DUOFOLD] Patched SettingsViewController.m; project.pbxproj was not touched.")
    print("[DUOFOLD] Backup: Cyanide/SettingsViewController.m.duofold.bak")

if __name__ == "__main__":
    main()
