/*
 * gold-15 改版 gatekeeper service.cpp（基于 AOSP
 * hardware-interfaces/gatekeeper/aidl/software/service.cpp）
 *
 * 差异（相对 AOSP 原版）：
 *   去掉 `ISharedSecret/gatekeeper` 实例的 addService —— 该实例名是
 *   Cuttlefish APEX（com.android.hardware.gatekeeper.nonsecure）特有的
 *   sharedsecret 服务约定，gold 官方 sepolicy 未授权 hal_gatekeeper_default
 *   域注册该跨 HAL 服务名（addService 必被 service manager 拒绝，随后
 *   CHECK_EQ abort，vendor.gatekeeper_mitee 变 crash 循环）。
 *   SoftSharedSecret 对象仍保留构造：SoftGateKeeper 的 HMAC auth token
 *   key 由其派生（GetAuthTokenKey），去掉对象会破坏 enroll/verify。
 *
 *   同时去掉未使用的 <cutils/properties.h> include。
 *
 * 上游原版其余逻辑一字未改。
 */
#define LOG_TAG "android.hardware.gatekeeper-service.nonsecure"

#include <android-base/logging.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>

#include "GateKeeper.h"
#include "SharedSecret.h"
#include "SoftGateKeeper.h"

using aidl::android::hardware::gatekeeper::SoftGateKeeperDevice;
using aidl::android::hardware::security::sharedsecret::SoftSharedSecret;

int main(int, char** argv) {
    ::android::base::InitLogging(argv, ::android::base::KernelLogger);
    ABinderProcess_setThreadPoolMaxThreadCount(0);

    auto secret = ndk::SharedRefBase::make<SoftSharedSecret>();

    ::gatekeeper::SoftGateKeeper implementation(*secret);
    auto gatekeeper = ndk::SharedRefBase::make<SoftGateKeeperDevice>(implementation);
    const std::string instance = SoftGateKeeperDevice::descriptor + std::string("/default");
    auto status = AServiceManager_addService(gatekeeper->asBinder().get(), instance.c_str());
    CHECK_EQ(status, STATUS_OK);

    ABinderProcess_joinThreadPool();
    return -1;  // Should never get here.
}
