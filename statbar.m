//
//  statbar.m
//  PerfHUD implementation built on Cyanide's existing StatBar lifecycle.
//  Keeps the public statbar_* API unchanged so Settings / cleanup / keep-alive
//  code does not need to be rewritten.
//

#import "statbar.h"
#import "remote_objc.h"
#import "../TaskRop/RemoteCall.h"
#import "../LogTextView.h"

#import <Foundation/Foundation.h>
#import <CoreFoundation/CoreFoundation.h>
#import <UIKit/UIKit.h>
#import <mach/mach.h>
#import <mach/mach_host.h>
#import <dlfcn.h>
#import <math.h>
#import <stdio.h>
#import <string.h>
#import <time.h>
#import <unistd.h>

typedef mach_port_t io_object_t;
typedef io_object_t io_service_t;
typedef io_object_t io_iterator_t;

// =============================================================================
// IOKit symbols
// =============================================================================

static void *g_iokit = NULL;
static CFMutableDictionaryRef (*pIOServiceMatching)(const char *) = NULL;
static kern_return_t (*pIOServiceGetMatchingServices)(mach_port_t, CFDictionaryRef, io_iterator_t *) = NULL;
static io_object_t (*pIOIteratorNext)(io_iterator_t) = NULL;
static CFTypeRef (*pIORegistryEntryCreateCFProperty)(io_service_t, CFStringRef, CFAllocatorRef, uint32_t) = NULL;
static kern_return_t (*pIOObjectRelease)(io_object_t) = NULL;
static bool gRemoteIOKitLoaded = false;

static bool ensure_iokit_symbols(void)
{
    if (!g_iokit) {
        g_iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY | RTLD_GLOBAL);
        if (!g_iokit) return false;

        pIOServiceMatching = dlsym(g_iokit, "IOServiceMatching");
        pIOServiceGetMatchingServices = dlsym(g_iokit, "IOServiceGetMatchingServices");
        pIOIteratorNext = dlsym(g_iokit, "IOIteratorNext");
        pIORegistryEntryCreateCFProperty = dlsym(g_iokit, "IORegistryEntryCreateCFProperty");
        pIOObjectRelease = dlsym(g_iokit, "IOObjectRelease");
    }

    return pIOServiceMatching &&
           pIOServiceGetMatchingServices &&
           pIOIteratorNext &&
           pIORegistryEntryCreateCFProperty &&
           pIOObjectRelease;
}

static bool ensure_remote_iokit_loaded(void)
{
    if (!ensure_iokit_symbols()) return false;

    if (gRemoteIOKitLoaded) return true;

    uint64_t path = r_alloc_str("/System/Library/Frameworks/IOKit.framework/IOKit");
    if (!path) return false;

    uint64_t handle = r_dlsym_call(R_TIMEOUT, "dlopen",
                                   path, RTLD_LAZY | RTLD_GLOBAL,
                                   0, 0, 0, 0, 0, 0);
    r_free(path);
    gRemoteIOKitLoaded = (handle != 0);
    return gRemoteIOKitLoaded;
}

// =============================================================================
// CPU / RAM metrics (local, system-wide)
// =============================================================================

static double read_cpu_percent(void)
{
    static bool havePrev = false;
    static natural_t prevTicks[CPU_STATE_MAX] = {0};

    mach_port_t host = mach_host_self();
    host_cpu_load_info_data_t info;
    mach_msg_type_number_t count = HOST_CPU_LOAD_INFO_COUNT;
    kern_return_t kr = host_statistics(host, HOST_CPU_LOAD_INFO,
                                       (host_info_t)&info, &count);
    mach_port_deallocate(mach_task_self(), host);
    if (kr != KERN_SUCCESS) return -1.0;

    if (!havePrev) {
        memcpy(prevTicks, info.cpu_ticks, sizeof(prevTicks));
        havePrev = true;
        return -1.0;
    }

    natural_t dUser = info.cpu_ticks[CPU_STATE_USER] - prevTicks[CPU_STATE_USER];
    natural_t dSys  = info.cpu_ticks[CPU_STATE_SYSTEM] - prevTicks[CPU_STATE_SYSTEM];
    natural_t dIdle = info.cpu_ticks[CPU_STATE_IDLE] - prevTicks[CPU_STATE_IDLE];
    natural_t dNice = info.cpu_ticks[CPU_STATE_NICE] - prevTicks[CPU_STATE_NICE];
    memcpy(prevTicks, info.cpu_ticks, sizeof(prevTicks));

    uint64_t busy = (uint64_t)dUser + (uint64_t)dSys + (uint64_t)dNice;
    uint64_t total = busy + (uint64_t)dIdle;
    if (total == 0) return -1.0;

    double pct = 100.0 * (double)busy / (double)total;
    return fmin(100.0, fmax(0.0, pct));
}

static double read_ram_percent(void)
{
    mach_port_t host = mach_host_self();
    vm_statistics64_data_t stat;
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    kern_return_t kr = host_statistics64(host, HOST_VM_INFO64,
                                         (host_info64_t)&stat, &count);
    mach_port_deallocate(mach_task_self(), host);
    if (kr != KERN_SUCCESS) return -1.0;

    uint64_t pageSize = (uint64_t)vm_kernel_page_size;
    uint64_t activeBytes = (uint64_t)stat.active_count * pageSize;
    uint64_t wiredBytes = (uint64_t)stat.wire_count * pageSize;
    uint64_t compressedBytes = (uint64_t)stat.compressor_page_count * pageSize;
    uint64_t usedBytes = activeBytes + wiredBytes + compressedBytes;

    uint64_t totalBytes = NSProcessInfo.processInfo.physicalMemory;
    if (totalBytes == 0) {
        uint64_t reclaimablePages = (uint64_t)stat.free_count +
                                    (uint64_t)stat.inactive_count +
                                    (uint64_t)stat.speculative_count;
        totalBytes = usedBytes + reclaimablePages * pageSize;
    }
    if (totalBytes == 0) return -1.0;

    double pct = 100.0 * (double)usedBytes / (double)totalBytes;
    return fmin(100.0, fmax(0.0, pct));
}

// =============================================================================
// GPU utilization
// =============================================================================

static io_service_t gLocalGPUService = MACH_PORT_NULL;
static uint64_t gRemoteGPUService = 0;
static uint64_t gRemoteGPUCFKeys[6] = {0};
static uint64_t gRemoteGPUNumberScratch = 0;
static time_t gLocalGPUProbeTime = 0;
static time_t gRemoteGPUProbeTime = 0;

static double clamp_gpu_percent(double value)
{
    if (!isfinite(value) || value < 0.0) return -1.0;
    return fmin(100.0, value);
}

static double local_number_from_stats(NSDictionary *stats, NSString *key)
{
    if (![stats isKindOfClass:NSDictionary.class] || key.length == 0) return -1.0;
    id raw = stats[key];
    if (![raw respondsToSelector:@selector(doubleValue)]) return -1.0;
    return clamp_gpu_percent([raw doubleValue]);
}

static double local_gpu_percent_from_service(io_service_t service)
{
    if (service == MACH_PORT_NULL || !ensure_iokit_symbols()) return -1.0;

    CFTypeRef prop = pIORegistryEntryCreateCFProperty(service,
                                                       CFSTR("PerformanceStatistics"),
                                                       kCFAllocatorDefault,
                                                       0);
    if (!prop) return -1.0;

    double result = -1.0;
    if (CFGetTypeID(prop) == CFDictionaryGetTypeID()) {
        NSDictionary *stats = (__bridge NSDictionary *)prop;

        result = local_number_from_stats(stats, @"Device Utilization %");
        if (result < 0.0) result = local_number_from_stats(stats, @"GPU Activity(%)");
        if (result < 0.0) result = local_number_from_stats(stats, @"GPU Utilization %");

        if (result < 0.0) {
            double renderer = local_number_from_stats(stats, @"Renderer Utilization %");
            double tiler = local_number_from_stats(stats, @"Tiler Utilization %");
            if (renderer >= 0.0 || tiler >= 0.0) {
                result = fmax(renderer >= 0.0 ? renderer : 0.0,
                              tiler >= 0.0 ? tiler : 0.0);
            }
        }
    }

    CFRelease(prop);
    return result;
}

static io_service_t find_local_gpu_service_for_class(const char *className)
{
    if (!className || !ensure_iokit_symbols()) return MACH_PORT_NULL;

    CFMutableDictionaryRef matching = pIOServiceMatching(className);
    if (!matching) return MACH_PORT_NULL;

    io_iterator_t iterator = MACH_PORT_NULL;
    kern_return_t kr = pIOServiceGetMatchingServices(MACH_PORT_NULL, matching, &iterator);
    if (kr != KERN_SUCCESS || iterator == MACH_PORT_NULL) return MACH_PORT_NULL;

    io_service_t chosen = MACH_PORT_NULL;
    for (int i = 0; i < 16; i++) {
        io_service_t service = pIOIteratorNext(iterator);
        if (service == MACH_PORT_NULL) break;

        CFTypeRef prop = pIORegistryEntryCreateCFProperty(service,
                                                           CFSTR("PerformanceStatistics"),
                                                           kCFAllocatorDefault,
                                                           0);
        if (prop) {
            bool isDictionary = (CFGetTypeID(prop) == CFDictionaryGetTypeID());
            CFRelease(prop);
            if (isDictionary) {
                chosen = service;
                break;
            }
        }

        pIOObjectRelease(service);
    }

    pIOObjectRelease(iterator);
    return chosen;
}

static double read_gpu_percent_local(void)
{
    if (!ensure_iokit_symbols()) return -1.0;

    if (gLocalGPUService != MACH_PORT_NULL) {
        double pct = local_gpu_percent_from_service(gLocalGPUService);
        if (pct >= 0.0) return pct;
    }

    time_t now = time(NULL);
    if (gLocalGPUProbeTime != 0 && now >= gLocalGPUProbeTime && (now - gLocalGPUProbeTime) < 15) {
        return -1.0;
    }
    gLocalGPUProbeTime = now;

    if (gLocalGPUService != MACH_PORT_NULL) {
        pIOObjectRelease(gLocalGPUService);
        gLocalGPUService = MACH_PORT_NULL;
    }

    const char *classes[] = { "IOAccelerator", "AGXAccelerator" };
    for (size_t i = 0; i < sizeof(classes) / sizeof(classes[0]); i++) {
        io_service_t service = find_local_gpu_service_for_class(classes[i]);
        if (service == MACH_PORT_NULL) continue;
        gLocalGPUService = service;
        double pct = local_gpu_percent_from_service(service);
        if (pct >= 0.0) return pct;
    }

    return -1.0;
}

static uint64_t remote_gpu_cfkey(int slot, const char *name)
{
    if (slot < 0 || slot >= 6 || !name) return 0;
    if (r_is_objc_ptr(gRemoteGPUCFKeys[slot])) return gRemoteGPUCFKeys[slot];
    uint64_t key = r_cfstr(name);
    if (r_is_objc_ptr(key)) gRemoteGPUCFKeys[slot] = key;
    return key;
}

static uint64_t remote_gpu_number_scratch(void)
{
    if (gRemoteGPUNumberScratch) return gRemoteGPUNumberScratch;
    gRemoteGPUNumberScratch = r_dlsym_call(R_TIMEOUT, "malloc", 8, 0, 0, 0, 0, 0, 0, 0);
    if (gRemoteGPUNumberScratch) remote_write64(gRemoteGPUNumberScratch, 0);
    return gRemoteGPUNumberScratch;
}

static void release_remote_gpu_cached_objects_in_session(void)
{
    for (int i = 0; i < 6; i++) {
        if (r_is_objc_ptr(gRemoteGPUCFKeys[i])) {
            r_dlsym_call(R_TIMEOUT, "CFRelease", gRemoteGPUCFKeys[i], 0, 0, 0, 0, 0, 0, 0);
        }
        gRemoteGPUCFKeys[i] = 0;
    }
    if (gRemoteGPUNumberScratch) r_free(gRemoteGPUNumberScratch);
    gRemoteGPUNumberScratch = 0;
}

static double remote_cfnumber_to_double(uint64_t number)
{
    if (!r_is_objc_ptr(number)) return -1.0;

    uint64_t scratch = remote_gpu_number_scratch();
    if (!scratch) return -1.0;

    remote_write64(scratch, 0);
    uint64_t ok = r_dlsym_call(R_TIMEOUT, "CFNumberGetValue",
                               number, (uint64_t)kCFNumberDoubleType, scratch,
                               0, 0, 0, 0, 0);

    double value = -1.0;
    if (ok) {
        uint64_t bits = remote_read64(scratch);
        memcpy(&value, &bits, sizeof(value));
    }

    return value;
}

static double remote_number_from_stats(uint64_t stats, int keySlot, const char *keyName)
{
    if (!r_is_objc_ptr(stats) || !keyName) return -1.0;

    uint64_t key = remote_gpu_cfkey(keySlot, keyName);
    if (!r_is_objc_ptr(key)) return -1.0;

    uint64_t number = r_msg2(stats, "objectForKey:", key, 0, 0, 0);
    if (!r_is_objc_ptr(number)) return -1.0;

    return clamp_gpu_percent(remote_cfnumber_to_double(number));
}

static double remote_gpu_percent_from_service(uint64_t service)
{
    if (!service || !ensure_remote_iokit_loaded()) return -1.0;

    uint64_t key = remote_gpu_cfkey(0, "PerformanceStatistics");
    if (!r_is_objc_ptr(key)) return -1.0;

    uint64_t stats = do_remote_call_stable_addr(R_TIMEOUT,
                                                (uint64_t)pIORegistryEntryCreateCFProperty,
                                                "IORegistryEntryCreateCFProperty",
                                                service, key, 0, 0,
                                                0, 0, 0, 0);
    if (!r_is_objc_ptr(stats)) return -1.0;

    double result = remote_number_from_stats(stats, 1, "Device Utilization %");
    if (result < 0.0) result = remote_number_from_stats(stats, 2, "GPU Activity(%)");
    if (result < 0.0) result = remote_number_from_stats(stats, 3, "GPU Utilization %");

    if (result < 0.0) {
        double renderer = remote_number_from_stats(stats, 4, "Renderer Utilization %");
        double tiler = remote_number_from_stats(stats, 5, "Tiler Utilization %");
        if (renderer >= 0.0 || tiler >= 0.0) {
            result = fmax(renderer >= 0.0 ? renderer : 0.0,
                          tiler >= 0.0 ? tiler : 0.0);
        }
    }

    r_dlsym_call(R_TIMEOUT, "CFRelease", stats, 0, 0, 0, 0, 0, 0, 0);
    return result;
}

static uint64_t find_remote_gpu_service_for_class(const char *className)
{
    if (!className || !ensure_remote_iokit_loaded()) return 0;

    uint64_t classStr = r_alloc_str(className);
    if (!classStr) return 0;

    uint64_t matching = do_remote_call_stable_addr(R_TIMEOUT,
                                                   (uint64_t)pIOServiceMatching,
                                                   "IOServiceMatching",
                                                   classStr, 0, 0, 0,
                                                   0, 0, 0, 0);
    r_free(classStr);
    if (!matching) return 0;

    uint64_t iteratorOut = r_dlsym_call(R_TIMEOUT, "malloc", 8, 0, 0, 0, 0, 0, 0, 0);
    if (!iteratorOut) return 0;
    remote_write64(iteratorOut, 0);

    uint64_t kr = do_remote_call_stable_addr(R_TIMEOUT,
                                             (uint64_t)pIOServiceGetMatchingServices,
                                             "IOServiceGetMatchingServices",
                                             MACH_PORT_NULL, matching, iteratorOut,
                                             0, 0, 0, 0, 0);
    uint64_t iterator = remote_read64(iteratorOut) & 0xffffffffULL;
    r_free(iteratorOut);
    if ((kern_return_t)kr != KERN_SUCCESS || !iterator) return 0;

    uint64_t chosen = 0;
    for (int i = 0; i < 16; i++) {
        uint64_t service = do_remote_call_stable_addr(R_TIMEOUT,
                                                      (uint64_t)pIOIteratorNext,
                                                      "IOIteratorNext",
                                                      iterator, 0, 0, 0,
                                                      0, 0, 0, 0);
        if (!service) break;

        uint64_t key = remote_gpu_cfkey(0, "PerformanceStatistics");
        uint64_t stats = 0;
        if (r_is_objc_ptr(key)) {
            stats = do_remote_call_stable_addr(R_TIMEOUT,
                                               (uint64_t)pIORegistryEntryCreateCFProperty,
                                               "IORegistryEntryCreateCFProperty",
                                               service, key, 0, 0,
                                               0, 0, 0, 0);
        }

        if (r_is_objc_ptr(stats)) {
            r_dlsym_call(R_TIMEOUT, "CFRelease", stats, 0, 0, 0, 0, 0, 0, 0);
            chosen = service;
            break;
        }

        do_remote_call_stable_addr(R_TIMEOUT,
                                   (uint64_t)pIOObjectRelease,
                                   "IOObjectRelease",
                                   service, 0, 0, 0,
                                   0, 0, 0, 0);
    }

    do_remote_call_stable_addr(R_TIMEOUT,
                               (uint64_t)pIOObjectRelease,
                               "IOObjectRelease",
                               iterator, 0, 0, 0,
                               0, 0, 0, 0);
    return chosen;
}

static double read_gpu_percent_remote(void)
{
    if (!ensure_remote_iokit_loaded()) return -1.0;

    if (gRemoteGPUService) {
        double pct = remote_gpu_percent_from_service(gRemoteGPUService);
        if (pct >= 0.0) return pct;
    }

    time_t now = time(NULL);
    if (gRemoteGPUProbeTime != 0 && now >= gRemoteGPUProbeTime && (now - gRemoteGPUProbeTime) < 15) {
        return -1.0;
    }
    gRemoteGPUProbeTime = now;

    if (gRemoteGPUService) {
        do_remote_call_stable_addr(R_TIMEOUT,
                                   (uint64_t)pIOObjectRelease,
                                   "IOObjectRelease",
                                   gRemoteGPUService, 0, 0, 0,
                                   0, 0, 0, 0);
        gRemoteGPUService = 0;
    }

    const char *classes[] = { "IOAccelerator", "AGXAccelerator" };
    for (size_t i = 0; i < sizeof(classes) / sizeof(classes[0]); i++) {
        uint64_t service = find_remote_gpu_service_for_class(classes[i]);
        if (!service) continue;
        gRemoteGPUService = service;
        double pct = remote_gpu_percent_from_service(service);
        if (pct >= 0.0) return pct;
    }

    return -1.0;
}

static double read_gpu_percent(void)
{
    double local = read_gpu_percent_local();
    if (local >= 0.0) return local;
    return read_gpu_percent_remote();
}

static void release_remote_gpu_service_in_session(void)
{
    if (!gRemoteGPUService || !ensure_iokit_symbols()) {
        gRemoteGPUService = 0;
        return;
    }

    do_remote_call_stable_addr(R_TIMEOUT,
                               (uint64_t)pIOObjectRelease,
                               "IOObjectRelease",
                               gRemoteGPUService, 0, 0, 0,
                               0, 0, 0, 0);
    gRemoteGPUService = 0;
}

// =============================================================================
// PerfHUD overlay
// =============================================================================

typedef struct {
    double x;
    double y;
    double width;
    double height;
} RCGRect64;

typedef struct {
    double width;
    double height;
} RCGSize64;

typedef struct {
    double r;
    double g;
    double b;
} PerfRGB;

static const uint64_t kPerfHUDCPUTag = 99501;
static const uint64_t kPerfHUDGPUTag = 99502;
static const uint64_t kPerfHUDRAMTag = 99503;
static const double kPerfHUDHeight = 24.0;
static const double kPerfHUDFontPt = 12.5;
static const double kPerfHUDLandscapeY = 7.0;
static const double kPerfHUDSideMargin = 8.0;
static const double kPerfHUDWindowLevel = 999999.0;

static uint64_t gStatBarApplyTick = 0;
static uint64_t gPerfHUDWindow = 0;
static uint64_t gPerfHUDCPULabel = 0;
static uint64_t gPerfHUDGPULabel = 0;
static uint64_t gPerfHUDRAMLabel = 0;
static uint64_t gPerfHUDNSStringClass = 0;
static uint64_t gPerfHUDAllocSel = 0;
static uint64_t gPerfHUDInitUTF8Sel = 0;
static uint64_t gPerfHUDSetTextSel = 0;
static uint64_t gPerfHUDPerformMainSel = 0;
static uint64_t gPerfHUDColorCache[5] = {0};
static uint64_t gPerfHUDInvalidColor = 0;
static int gPerfHUDLastColorBucket[3] = { -999, -999, -999 };

static bool statbar_should_log_tick(void)
{
    return gStatBarApplyTick == 1;
}

static bool r_send_double_main(uint64_t obj, const char *selName, double value)
{
    if (!r_is_objc_ptr(obj)) return false;
    r_msg2_main_raw(obj, selName,
                    &value, sizeof(value),
                    NULL, 0,
                    NULL, 0,
                    NULL, 0);
    usleep(15000);
    return true;
}

static bool r_send_rect_main(uint64_t obj, const char *selName,
                             double x, double y, double width, double height)
{
    if (!r_is_objc_ptr(obj)) return false;
    RCGRect64 rect = { x, y, width, height };
    r_msg2_main_raw(obj, selName,
                    &rect, sizeof(rect),
                    NULL, 0,
                    NULL, 0,
                    NULL, 0);
    usleep(15000);
    return true;
}

static bool r_send_size_main(uint64_t obj, const char *selName,
                             double width, double height)
{
    if (!r_is_objc_ptr(obj)) return false;
    RCGSize64 size = { width, height };
    r_msg2_main_raw(obj, selName,
                    &size, sizeof(size),
                    NULL, 0,
                    NULL, 0,
                    NULL, 0);
    usleep(15000);
    return true;
}

static uint64_t perfhud_nsstring_utf8(const char *cstr)
{
    if (!cstr) cstr = "--";
    uint64_t buf = r_alloc_str(cstr);
    if (!buf) return 0;

    if (!gPerfHUDNSStringClass) gPerfHUDNSStringClass = r_class("NSString");
    if (!gPerfHUDAllocSel) gPerfHUDAllocSel = r_sel("alloc");
    if (!gPerfHUDInitUTF8Sel) gPerfHUDInitUTF8Sel = r_sel("initWithUTF8String:");

    if (!r_is_objc_ptr(gPerfHUDNSStringClass) || !gPerfHUDAllocSel || !gPerfHUDInitUTF8Sel) {
        r_free(buf);
        return 0;
    }

    uint64_t allocated = r_msg(gPerfHUDNSStringClass, gPerfHUDAllocSel, 0, 0, 0, 0);
    uint64_t ns = r_is_objc_ptr(allocated)
        ? r_msg(allocated, gPerfHUDInitUTF8Sel, buf, 0, 0, 0)
        : 0;
    r_free(buf);
    return ns;
}

static void perfhud_release_remote_obj(uint64_t obj)
{
    if (!r_is_objc_ptr(obj)) return;
    r_dlsym_call(R_TIMEOUT, "CFRelease", obj, 0, 0, 0, 0, 0, 0, 0);
}

static bool perfhud_set_text_fast(uint64_t label, uint64_t textObj)
{
    if (!r_is_objc_ptr(label) || !r_is_objc_ptr(textObj)) return false;

    if (!gPerfHUDSetTextSel) gPerfHUDSetTextSel = r_sel("setText:");
    if (!gPerfHUDPerformMainSel) {
        gPerfHUDPerformMainSel = r_sel("performSelectorOnMainThread:withObject:waitUntilDone:");
    }
    if (!gPerfHUDSetTextSel || !gPerfHUDPerformMainSel) return false;

    r_msg(label, gPerfHUDPerformMainSel, gPerfHUDSetTextSel, textObj, 1, 0);
    return true;
}

static int perfhud_color_bucket(double pct)
{
    if (pct < 60.0) return 0;   // soft green
    if (pct < 70.0) return 1;   // yellow
    if (pct < 85.0) return 2;   // orange
    if (pct < 95.0) return 3;   // orange-red
    return 4;                    // red
}

static PerfRGB perfhud_rgb_for_bucket(int bucket)
{
    switch (bucket) {
        case 0: return (PerfRGB){ 0.49, 0.92, 0.58 };
        case 1: return (PerfRGB){ 0.95, 0.84, 0.36 };
        case 2: return (PerfRGB){ 1.00, 0.61, 0.27 };
        case 3: return (PerfRGB){ 1.00, 0.36, 0.22 };
        default:return (PerfRGB){ 1.00, 0.18, 0.18 };
    }
}

static uint64_t perfhud_make_color(PerfRGB rgb, double alpha)
{
    uint64_t UIColor = r_class("UIColor");
    if (!r_is_objc_ptr(UIColor)) return 0;

    return r_msg2_main_raw(UIColor, "colorWithRed:green:blue:alpha:",
                           &rgb.r, sizeof(rgb.r),
                           &rgb.g, sizeof(rgb.g),
                           &rgb.b, sizeof(rgb.b),
                           &alpha, sizeof(alpha));
}

static uint64_t perfhud_color_for_percent(double pct)
{
    if (!isfinite(pct) || pct < 0.0) {
        if (r_is_objc_ptr(gPerfHUDInvalidColor)) return gPerfHUDInvalidColor;
        PerfRGB gray = { 0.82, 0.82, 0.84 };
        uint64_t c = perfhud_make_color(gray, 0.90);
        if (r_is_objc_ptr(c)) {
            r_msg2(c, "retain", 0, 0, 0, 0);
            gPerfHUDInvalidColor = c;
        }
        return c;
    }

    int bucket = perfhud_color_bucket(pct);
    if (r_is_objc_ptr(gPerfHUDColorCache[bucket])) return gPerfHUDColorCache[bucket];

    PerfRGB rgb = perfhud_rgb_for_bucket(bucket);
    uint64_t c = perfhud_make_color(rgb, 1.0);
    if (r_is_objc_ptr(c)) {
        r_msg2(c, "retain", 0, 0, 0, 0);
        gPerfHUDColorCache[bucket] = c;
    }
    return c;
}

static void perfhud_forget_color_cache(void)
{
    memset(gPerfHUDColorCache, 0, sizeof(gPerfHUDColorCache));
    gPerfHUDInvalidColor = 0;
}

static void perfhud_release_color_cache_in_session(void)
{
    for (int i = 0; i < 5; i++) {
        uint64_t c = gPerfHUDColorCache[i];
        if (r_is_objc_ptr(c)) r_msg2(c, "release", 0, 0, 0, 0);
        gPerfHUDColorCache[i] = 0;
    }
    if (r_is_objc_ptr(gPerfHUDInvalidColor)) {
        r_msg2(gPerfHUDInvalidColor, "release", 0, 0, 0, 0);
    }
    gPerfHUDInvalidColor = 0;
}

static uint64_t perfhud_font(void)
{
    uint64_t UIFont = r_class("UIFont");
    if (!r_is_objc_ptr(UIFont)) return 0;

    double size = kPerfHUDFontPt;
    double weight = 0.25;
    uint64_t font = r_msg2_main_raw(UIFont, "monospacedDigitSystemFontOfSize:weight:",
                                    &size, sizeof(size),
                                    &weight, sizeof(weight),
                                    NULL, 0,
                                    NULL, 0);
    if (r_is_objc_ptr(font)) return font;

    return r_msg2_main_raw(UIFont, "systemFontOfSize:",
                           &size, sizeof(size),
                           NULL, 0,
                           NULL, 0,
                           NULL, 0);
}

static void perfhud_style_label(uint64_t label)
{
    if (!r_is_objc_ptr(label)) return;

    uint64_t font = perfhud_font();
    if (r_is_objc_ptr(font)) r_msg2_main(label, "setFont:", font, 0, 0, 0);

    uint64_t UIColor = r_class("UIColor");
    if (r_is_objc_ptr(UIColor)) {
        uint64_t clear = r_msg2_main(UIColor, "clearColor", 0, 0, 0, 0);
        uint64_t black = r_msg2_main(UIColor, "blackColor", 0, 0, 0, 0);
        if (r_is_objc_ptr(clear)) r_msg2_main(label, "setBackgroundColor:", clear, 0, 0, 0);
        if (r_is_objc_ptr(black)) r_msg2_main(label, "setShadowColor:", black, 0, 0, 0);
    }

    r_send_size_main(label, "setShadowOffset:", 0.0, 1.0);
    r_msg2_main(label, "setTextAlignment:", 1, 0, 0, 0);
    r_msg2_main(label, "setNumberOfLines:", 1, 0, 0, 0);
    r_msg2_main(label, "setAdjustsFontSizeToFitWidth:", 1, 0, 0, 0);
    r_send_double_main(label, "setMinimumScaleFactor:", 0.75);
    r_msg2_main(label, "setUserInteractionEnabled:", 0, 0, 0, 0);
}

static bool perfhud_is_landscape(uint64_t win)
{
    if (!r_is_objc_ptr(win)) return false;
    uint64_t scene = r_msg2_main(win, "windowScene", 0, 0, 0, 0);
    if (!r_is_objc_ptr(scene)) return false;
    uint64_t orientation = r_msg2_main(scene, "interfaceOrientation", 0, 0, 0, 0);
    return orientation == 3 || orientation == 4;
}

static double perfhud_portrait_y(double screenWidth, double screenHeight)
{
    double shortSide = fmin(screenWidth, screenHeight);
    double longSide = fmax(screenWidth, screenHeight);

    double topArea = 20.0;
    if (longSide >= 852.0 && shortSide >= 390.0) topArea = 59.0;
    else if (longSide >= 844.0 && shortSide >= 390.0) topArea = 47.0;
    else if (longSide >= 812.0 && shortSide >= 375.0) topArea = 44.0;

    if (topArea >= 36.0) return fmax(2.0, floor(topArea - (kPerfHUDHeight / 2.0)));
    return fmax(2.0, floor((topArea - kPerfHUDHeight) / 2.0));
}

static bool perfhud_apply_layout(uint64_t win,
                                 uint64_t cpuLabel,
                                 uint64_t gpuLabel,
                                 uint64_t ramLabel)
{
    if (!r_is_objc_ptr(win)) return false;

    CGRect bounds = UIScreen.mainScreen.bounds;
    double localW = fabs(bounds.size.width);
    double localH = fabs(bounds.size.height);
    if (localW < 100.0 || localH < 100.0) {
        localW = 390.0;
        localH = 844.0;
    }

    bool landscape = perfhud_is_landscape(win);
    double screenWidth = landscape ? fmax(localW, localH) : fmin(localW, localH);
    double screenHeight = landscape ? fmin(localW, localH) : fmax(localW, localH);

    double width = landscape ? 286.0 : 276.0;
    width = fmin(width, fmax(1.0, screenWidth - (kPerfHUDSideMargin * 2.0)));
    double x = floor((screenWidth - width) / 2.0);
    double y = landscape ? kPerfHUDLandscapeY : perfhud_portrait_y(screenWidth, screenHeight);

    bool ok = true;
    ok &= r_send_rect_main(win, "setFrame:", x, y, width, kPerfHUDHeight);
    ok &= r_send_double_main(win, "setWindowLevel:", kPerfHUDWindowLevel);
    r_msg2_main(win, "setUserInteractionEnabled:", 0, 0, 0, 0);

    double gap = 4.0;
    double slotW = (width - gap * 2.0) / 3.0;
    if (r_is_objc_ptr(cpuLabel)) ok &= r_send_rect_main(cpuLabel, "setFrame:", 0.0, 0.0, slotW, kPerfHUDHeight);
    if (r_is_objc_ptr(gpuLabel)) ok &= r_send_rect_main(gpuLabel, "setFrame:", slotW + gap, 0.0, slotW, kPerfHUDHeight);
    if (r_is_objc_ptr(ramLabel)) ok &= r_send_rect_main(ramLabel, "setFrame:", (slotW + gap) * 2.0, 0.0, slotW, kPerfHUDHeight);

    return ok;
}

static uint64_t perfhud_create_label(uint64_t tag)
{
    uint64_t UILabel = r_class("UILabel");
    if (!r_is_objc_ptr(UILabel)) return 0;

    uint64_t alloc = r_msg2_main(UILabel, "alloc", 0, 0, 0, 0);
    uint64_t label = r_is_objc_ptr(alloc)
        ? r_msg2_main(alloc, "init", 0, 0, 0, 0)
        : 0;
    if (!r_is_objc_ptr(label)) return 0;

    r_msg2_main(label, "setTag:", tag, 0, 0, 0);
    perfhud_style_label(label);
    return label;
}

static bool perfhud_find_or_create_overlay(void)
{
    if (r_is_objc_ptr(gPerfHUDWindow) &&
        r_is_objc_ptr(gPerfHUDCPULabel) &&
        r_is_objc_ptr(gPerfHUDGPULabel) &&
        r_is_objc_ptr(gPerfHUDRAMLabel)) {
        return true;
    }

    uint64_t UIApplication = r_class("UIApplication");
    if (!r_is_objc_ptr(UIApplication)) return false;

    uint64_t app = r_msg2_main(UIApplication, "sharedApplication", 0, 0, 0, 0);
    if (!r_is_objc_ptr(app)) return false;

    // Keep the original association key so old StatBar cleanup and upgrades
    // can still find and remove a window created by a previous build.
    uint64_t assocKey = r_sel("darkswordStatBarOverlayWindow");
    if (!assocKey) return false;

    uint64_t cachedWin = r_dlsym_call(R_TIMEOUT, "objc_getAssociatedObject",
                                      app, assocKey, 0, 0, 0, 0, 0, 0);
    if (r_is_objc_ptr(cachedWin)) {
        uint64_t cpu = r_msg2_main(cachedWin, "viewWithTag:", kPerfHUDCPUTag, 0, 0, 0);
        uint64_t gpu = r_msg2_main(cachedWin, "viewWithTag:", kPerfHUDGPUTag, 0, 0, 0);
        uint64_t ram = r_msg2_main(cachedWin, "viewWithTag:", kPerfHUDRAMTag, 0, 0, 0);
        if (r_is_objc_ptr(cpu) && r_is_objc_ptr(gpu) && r_is_objc_ptr(ram)) {
            gPerfHUDWindow = cachedWin;
            gPerfHUDCPULabel = cpu;
            gPerfHUDGPULabel = gpu;
            gPerfHUDRAMLabel = ram;
            r_msg2_main(cachedWin, "setHidden:", 0, 0, 0, 0);
            return true;
        }

        // Old single-label StatBar window or a half-built HUD. Hide it and
        // remove the association before constructing a clean three-label HUD.
        r_msg2_main(cachedWin, "setHidden:", 1, 0, 0, 0);
        r_dlsym_call(R_TIMEOUT, "objc_setAssociatedObject",
                     app, assocKey, 0, 1, 0, 0, 0, 0);
    }

    uint64_t keyWin = r_msg2_main(app, "keyWindow", 0, 0, 0, 0);
    if (!r_is_objc_ptr(keyWin)) {
        uint64_t windows = r_msg2_main(app, "windows", 0, 0, 0, 0);
        uint64_t count = r_is_objc_ptr(windows)
            ? r_msg2_main(windows, "count", 0, 0, 0, 0)
            : 0;
        if (count > 0 && count < 64) {
            keyWin = r_msg2_main(windows, "objectAtIndex:", 0, 0, 0, 0);
        }
    }
    if (!r_is_objc_ptr(keyWin)) return false;

    uint64_t scene = r_msg2_main(keyWin, "windowScene", 0, 0, 0, 0);
    if (!r_is_objc_ptr(scene)) return false;

    uint64_t UIWindow = r_class("UIWindow");
    if (!r_is_objc_ptr(UIWindow)) return false;

    uint64_t winAlloc = r_msg2_main(UIWindow, "alloc", 0, 0, 0, 0);
    uint64_t win = r_is_objc_ptr(winAlloc)
        ? r_msg2_main(winAlloc, "initWithWindowScene:", scene, 0, 0, 0)
        : 0;
    if (!r_is_objc_ptr(win)) return false;

    uint64_t UIColor = r_class("UIColor");
    if (r_is_objc_ptr(UIColor)) {
        uint64_t clear = r_msg2_main(UIColor, "clearColor", 0, 0, 0, 0);
        if (r_is_objc_ptr(clear)) r_msg2_main(win, "setBackgroundColor:", clear, 0, 0, 0);
    }

    uint64_t cpu = perfhud_create_label(kPerfHUDCPUTag);
    uint64_t gpu = perfhud_create_label(kPerfHUDGPUTag);
    uint64_t ram = perfhud_create_label(kPerfHUDRAMTag);
    if (!r_is_objc_ptr(cpu) || !r_is_objc_ptr(gpu) || !r_is_objc_ptr(ram)) {
        r_msg2_main(win, "setHidden:", 1, 0, 0, 0);
        return false;
    }

    r_msg2_main(win, "addSubview:", cpu, 0, 0, 0);
    r_msg2_main(win, "addSubview:", gpu, 0, 0, 0);
    r_msg2_main(win, "addSubview:", ram, 0, 0, 0);

    perfhud_apply_layout(win, cpu, gpu, ram);
    r_msg2_main(win, "setHidden:", 0, 0, 0, 0);
    r_dlsym_call(R_TIMEOUT, "objc_setAssociatedObject",
                 app, assocKey, win, 1, 0, 0, 0, 0);

    gPerfHUDWindow = win;
    gPerfHUDCPULabel = cpu;
    gPerfHUDGPULabel = gpu;
    gPerfHUDRAMLabel = ram;

    if (statbar_should_log_tick()) {
        printf("[PERFHUD] installed transparent 3-metric overlay\n");
    }
    return true;
}

static bool perfhud_update_label(uint64_t label,
                                 int metricIndex,
                                 const char *name,
                                 double percent)
{
    if (!r_is_objc_ptr(label) || !name) return false;

    char text[32];
    if (isfinite(percent) && percent >= 0.0) {
        snprintf(text, sizeof(text), "%s %lld%%", name, (long long)llround(percent));
    } else {
        snprintf(text, sizeof(text), "%s --", name);
    }

    uint64_t textObj = perfhud_nsstring_utf8(text);
    if (!r_is_objc_ptr(textObj)) return false;

    bool ok = perfhud_set_text_fast(label, textObj);
    perfhud_release_remote_obj(textObj);

    int bucket = (!isfinite(percent) || percent < 0.0) ? -1 : perfhud_color_bucket(percent);
    if (metricIndex < 0 || metricIndex >= 3 || gPerfHUDLastColorBucket[metricIndex] != bucket) {
        uint64_t color = perfhud_color_for_percent(percent);
        if (r_is_objc_ptr(color)) {
            r_msg2_main(label, "setTextColor:", color, 0, 0, 0);
            if (metricIndex >= 0 && metricIndex < 3) gPerfHUDLastColorBucket[metricIndex] = bucket;
        }
    }
    return ok;
}

static bool perfhud_update(double cpu, double gpu, double ram)
{
    if (!perfhud_find_or_create_overlay()) return false;

    bool ok = true;
    ok &= perfhud_update_label(gPerfHUDCPULabel, 0, "CPU", cpu);
    ok &= perfhud_update_label(gPerfHUDGPULabel, 1, "GPU", gpu);
    ok &= perfhud_update_label(gPerfHUDRAMLabel, 2, "RAM", ram);
    ok &= perfhud_apply_layout(gPerfHUDWindow,
                               gPerfHUDCPULabel,
                               gPerfHUDGPULabel,
                               gPerfHUDRAMLabel);
    return ok;
}

// =============================================================================
// Public StatBar API kept intact for Cyanide SettingsViewController
// =============================================================================

bool statbar_stop_in_session(void)
{
    release_remote_gpu_service_in_session();
    release_remote_gpu_cached_objects_in_session();
    perfhud_release_color_cache_in_session();

    uint64_t UIApplication = r_class("UIApplication");
    if (!r_is_objc_ptr(UIApplication)) return false;

    uint64_t app = r_msg2_main(UIApplication, "sharedApplication", 0, 0, 0, 0);
    if (!r_is_objc_ptr(app)) return false;

    uint64_t assocKey = r_sel("darkswordStatBarOverlayWindow");
    if (!assocKey) return false;

    uint64_t win = r_dlsym_call(R_TIMEOUT, "objc_getAssociatedObject",
                                app, assocKey, 0, 0, 0, 0, 0, 0);
    if (r_is_objc_ptr(win)) {
        r_msg2_main(win, "setHidden:", 1, 0, 0, 0);
        r_dlsym_call(R_TIMEOUT, "objc_setAssociatedObject",
                     app, assocKey, 0, 1, 0, 0, 0, 0);
    }

    gPerfHUDWindow = 0;
    gPerfHUDCPULabel = 0;
    gPerfHUDGPULabel = 0;
    gPerfHUDRAMLabel = 0;
    gPerfHUDLastColorBucket[0] = -999;
    gPerfHUDLastColorBucket[1] = -999;
    gPerfHUDLastColorBucket[2] = -999;
    gRemoteIOKitLoaded = false;
    printf("[PERFHUD] overlay stopped\n");
    return true;
}

void statbar_forget_remote_state(void)
{
    // Called when SpringBoard / RemoteCall has already disappeared. Do not make
    // cleanup calls into dead remote objects here; just forget every pointer.
    gRemoteGPUService = 0;
    memset(gRemoteGPUCFKeys, 0, sizeof(gRemoteGPUCFKeys));
    gRemoteGPUNumberScratch = 0;
    gRemoteGPUProbeTime = 0;
    gRemoteIOKitLoaded = false;

    gPerfHUDWindow = 0;
    gPerfHUDCPULabel = 0;
    gPerfHUDGPULabel = 0;
    gPerfHUDRAMLabel = 0;
    gPerfHUDLastColorBucket[0] = -999;
    gPerfHUDLastColorBucket[1] = -999;
    gPerfHUDLastColorBucket[2] = -999;
    gPerfHUDNSStringClass = 0;
    gPerfHUDAllocSel = 0;
    gPerfHUDInitUTF8Sel = 0;
    gPerfHUDSetTextSel = 0;
    gPerfHUDPerformMainSel = 0;
    perfhud_forget_color_cache();

    printf("[PERFHUD] forgot remote overlay state\n");
}

bool statbar_apply_in_session(bool celsius,
                              bool showNet,
                              bool showCPU,
                              bool showLabels,
                              bool networkOnly)
{
    // The legacy arguments are intentionally kept for ABI/source compatibility
    // with Cyanide's Settings live-loop. PerfHUD always displays CPU/GPU/RAM.
    (void)celsius;
    (void)showNet;
    (void)showCPU;
    (void)showLabels;
    (void)networkOnly;

    gStatBarApplyTick++;

    double cpu = read_cpu_percent();
    double ram = read_ram_percent();
    double gpu = read_gpu_percent();

    if (statbar_should_log_tick()) {
        printf("[PERFHUD] first sample cpu=%.1f gpu=%.1f ram=%.1f\n", cpu, gpu, ram);
    }

    return perfhud_update(cpu, gpu, ram);
}

bool statbar_apply(bool celsius,
                   bool showNet,
                   bool showCPU,
                   bool showLabels,
                   bool networkOnly)
{
    if (init_remote_call("SpringBoard", false) != 0) {
        printf("[PERFHUD] init_remote_call(SpringBoard) failed\n");
        return false;
    }

    bool ok = statbar_apply_in_session(celsius, showNet, showCPU, showLabels, networkOnly);
    destroy_remote_call();
    return ok;
}
