# android.hardware.audio —— gold-15 自研 audio HAL（AIDL v2）

> **设备集成层，不重复驱动源码、不自行编译链接**：驱动 C++（含两个 main）与整套 codegen +
> 编译 + 链接流水线都住在 `hal/android15/android.hardware.audio`；本模块的 `build.py` 是一个
> **薄集成层**——加载该线 `build.py:build(out_dir, runtime_lib_dir, dry_run)` 产出两个服务二进制，
> 再拷进 `/vendor` staging、落 gold 独有配置（本目录 `vendor/`）并登记 SELinux。运行库 ABI 由
> gold-15 lineage-22.0 super 研磨（或 env `CDH_RUNTIME` 借用），经 `runtime_lib_dir` 传入引擎。
> 本目录**只放设备独有配置 `vendor/`**，不再自带 `src/`、`aidl/`、`deps.py`，也不复制编译流水线。

## 0. 模块定位

de-apex 提取的 lineage audio 服务闭包依赖 `libxml2 -> MIUI /system 版 libxml2 ->
libandroidicu`（vendor 命名空间不可达）导致 `CANNOT LINK`。本方案改为**自研服务**：
实现只依赖 `libbinder_ndk / libc++ / liblog`，闭包内根本没有 `libxml2`。

提供 `android.hardware.audio.core` v2 与 `android.hardware.audio.effect` v2 两套 AIDL 服务，
承载 guest 全部播放/录音/音量/音效，并把 PCM 同时喂给 VirtualDac（捕获回显）与
QEMU intel-hda 声卡（host 出声 + 时钟锚定）。

## 1. 服务与 VINTF 实例

两个 PIE 服务二进制（`vendor/etc/init/android.hardware.audio.service-aidl.example.rc`）：

| init 服务 | 二进制 | 用户 |
|---|---|---|
| `vendor.audio-hal-aidl` | `android.hardware.audio.service-aidl.example` | audioserver |
| `vendor.audio-effect-hal-aidl` | `android.hardware.audio.effect.service-aidl.example` | audioserver |

VINTF 声明（`vendor/etc/vintf/manifest/android.hardware.audio.service-aidl.xml`）：

| HAL | 版本 | 实例 |
|---|---|---|
| `android.hardware.audio.core` | 2 | `IModule/default`、`IModule/primary`、`IModule/r_submix`、`IConfig/default` |
| `android.hardware.audio.effect` | 2 | `IFactory/default` |

`r_submix` 实例供 REMOTE_SUBMIX（scrcpy/推流）捕获；`primary` 承载扬声器主输出。

## 2. 音量组配置（ConfigImpl）

`IConfig.getEngineConfig()` 的返回直接影响 framework 音量组行为。两个通用约束（与
note13 一致）：

1. **卷组名 = 引擎默认策略引用的流类型字符串**（`AUDIO_STREAM_MUSIC` / `AUDIO_STREAM_VOICE_CALL`
   …）。引擎默认策略（`EngineDefaultConfig.h` 的 `gOrderedStrategies`）用
   `attributesGroup.volumeGroupName` 按名引用卷组；名字对不上时 `processParsingResult`
   会建空组（Min/Max=0）并打 `No configuration of %s found`。
2. **`productStrategies` 必须为空**。小米闭源扩展 `libaudiopolicycustomextensions.so` 的
   `AudioPolicyManagerCustomImpl::gainTable_getVolumeDbFromComputeVolume` 在 HAL 提供
   productStrategies 时会 `map::at key not found` 崩溃 audioserver；留空则引擎用内置全集。

**本模块采用 `hal/android15` 的泛化 `ConfigImpl`**：`getEngineConfig()` 返回最小默认
引擎配置（`volumeGroups` / `productStrategies` 均为空），音量交由 `/vendor/etc` 的小米
引擎音量配置链驱动（见下）。这与 note13-5G-15 的做法不同——后者在 `ConfigImpl` 里硬编码
13 个卷组（名字精确匹配流类型，曲线取自 `audio_policy_engine_default_stream_volumes.xml`）。
若 gold-15 上出现音量组 Min/Max=0 / 音量键无效，可参考 note13 版 `ConfigImpl` 回填机型卷组。

配套把小米引擎音量配置链镜像到 `/vendor/etc`（MTK 默认引擎硬编码读取）：
`audio_policy_engine_configuration.xml`（含 `xi:include`）+ `product_strategies` /
`stream_volumes` / `default_stream_volumes` 三件，以及 `primary_audio_policy_configuration.xml`。

## 3. 音频数据通路（VdacStream 双路）

`VdacStreamOut::transfer()` 把每帧 PCM 同时落两条路径：

1. **VirtualDac**（进程内环形缓冲）—— REMOTE_SUBMIX 捕获端（scrcpy/推流）读回声，不阻塞；
2. **QEMU intel-hda 声卡**（ALSA `/dev/snd/pcmC0D0p`，`AlsaPcm`）—— `write` 按声卡消费节奏
   阻塞，把 AudioFlinger 的 burst 背压锚定到真实声卡时钟，修复音频时钟偏快（1.25x）导致的
   在线播放 2 倍速快进（详见 `build/doc/evidence/issue-video-fast-playback/solution.md`）。

ALSA 打开失败时回退纯 VirtualDac，不阻断播放。硬件音量（`setHwVolume` -> `mHwVolumes`，
`mVolumeMutex` 保护）逐声道线性增益；实际音量由 AudioFlinger 数字域处理（卷组见 §2），
本通路默认 unity 直通。

## 4. host 出声链路（QEMU 侧，非本模块）

guest PCM -> ALSA `/dev/snd/pcmC0D0p` -> QEMU intel-hda -> mixeng -> coreaudio -> host 扬声器。
两处 QEMU 侧关键配置（在 `core/host/daemon/qemu/qemu.go` 与 `qemu/source/audio/coreaudio.m`）：

- **`hda-output,...,mixer=off`**：禁用 HDA codec 功放仿真。guest 内核 HDA 驱动会下发
  AC_AMP 静音动词（jack 自动静音/驱动默认），把 mixeng SW voice 音量置 0（`mute=1`）导致
  host 静音；本项目音量已在 Android 数字域处理，codec 功放层多余且有害，故关闭，PCM 以
  unity 直通。
- **coreaudio 用 `DefaultSystemOutputDevice`**（系统扬声器）而非 `DefaultOutputDevice`
  （可能是无声的 AirPlay/蓝牙），避免 guest 音频被路由到无消费设备、ALSA 写阻塞卡死
  AudioFlinger。

## 5. 依赖来源

本模块**不复制编译流水线**：`build.py` 加载并调用 `hal/android15/android.hardware.audio/build.py`
的 `build(out_dir, runtime_lib_dir, dry_run)`，codegen + 编译 + 链接全在引擎里。本目录无
`src/`、`aidl/`、`deps.py`。分工：

- **构建引擎**：`hal/android15/android.hardware.audio/build.py`（本模块 `_load_by_path` 后调用）。
  引擎内部自取驱动源码、AIDL 快照、platform 头、编译期公共组件与工具链：
  - 源码 `hal/android15/android.hardware.audio/src`（含两个 main 与全部业务实现）；
  - AIDL `hal/android15/out/aosp/aidl/audio`、platform 头 `hal/android15/out/aosp/platform`
    （均由 `python3 hal/android15/build/deps.py audio --platform` 下载，引擎只读不拉）；
  - 编译期公共组件 `hal/android15/common/`（binder 平台头 / log shim / `__config_site` /
    bionic15 源码态 crt），env `CDH_HAL_COMMON` 可覆盖；工具链经 `hal/android15/build/toolchina.py`。
- **本模块只负责**：把引擎产出的二进制拷进 `/vendor` staging、落 gold 独有配置 `vendor/`
  （rc/vintf/policy/engine XML）、登记 SELinux baselines。
- **链接期 ABI 运行库**：`libc++.so / libbinder_ndk.so`（带 `.abi.so`）由 gold-15 自己
  lineage-22.0 super 研磨（`build/vendor/build/deps.py:prepare_runtime_libs`），经参数
  `runtime_lib_dir` 传入引擎；env `CDH_RUNTIME` 可指向已研磨目录。

## 6. 构建期依赖一览

| 依赖 | 来源 | 就绪判据 |
|---|---|---|
| 驱动源码 | `hal/android15/android.hardware.audio/src`（非本目录） | 目录存在 |
| AIDL 快照 | `hal/android15/out/aosp/aidl/audio`（hal 线 deps.py 下载） | `hal_deps.aidl_ready("audio")` |
| AOSP platform 头 | `hal/android15/out/aosp/platform`（hal 线 deps.py 下载） | `hal_deps.platform_ready()` |
| 编译期公共组件 | `hal/android15/common/`（含 bionic15 源码态 crt） | 目录存在 |
| ABI 运行库 | gold-15 lineage-22.0 super（共享 deps 研磨）或 `CDH_RUNTIME` | `libc++.so`/`libbinder_ndk.so` |

版本化包（`AIDL_PACKAGES`，hal 布局 `out/aosp/aidl/audio/<pkg>/<version>` + `.hash`）：
`android.hardware.common`(v2)、`android.hardware.common.fmq`(v1)、
`android.hardware.audio.common`(v2)、`android.hardware.audio.core`(v2)、
`android.hardware.audio.core.sounddose`(v1)、`android.hardware.audio.effect`(v2)；
另含 framework 稳定类型 `android.media.audio.common`（无版本，直接在 AIDL 根下）。

## 7. 构建编排（build.py）

本模块 `build.py` 是**薄集成层**，流程：

1. **定位运行库**：`prepare_abi()` 取 `runtime_lib_dir`（`CDH_RUNTIME` 优先，否则研磨
   gold-15 super）。
2. **委托引擎**：调用 `hal_audio.build(out_dir, runtime_lib_dir, dry_run)`，把整套
   codegen + 编译 + 链接交给 `hal/android15/android.hardware.audio/build.py`（引擎内含依赖
   校验 `check_deps`、NDK codegen、源码编译、源码态 crt 链接）。`out_dir` 传 `out/vendor`：
   引擎产物落 `out/vendor/bin/hw`，临时工作区落 `out/vendor/tmp/audio-ndk-work`（**不进**
   `out/dist/vendor` 打包面）。
3. **安装二进制**：`install_binaries()` 把两个服务从 `out/vendor/bin/hw` 拷进
   `out/dist/vendor/bin/hw`（`0755`）。
4. **落 gold 配置**：`write_rc / write_vintf / write_policy / write_engine_configs` 把本目录
   `vendor/` 下的 rc / vintf manifest / primary policy / 引擎音量 XML 链拷进 staging `/vendor/etc`。
5. **登记 SELinux**：`register_labels()` 把标签写进 baselines 清单（见 §9）。

工具链、AIDL 快照布局、`FmqSupport.cpp`（自实现 ashmem/native_handle/EventFlag）、
源码态 crt 链接等细节均归 `hal/android15/android.hardware.audio`，本模块不再重复。

## 8. 产物

落 `VENDOR_STAGE_DIR`（相对路径即镜像内 `/vendor` 路径）：

```
bin/hw/android.hardware.audio.service-aidl.example          (0755, hal_audio_default_exec)
bin/hw/android.hardware.audio.effect.service-aidl.example   (0755, hal_audio_default_exec)
etc/init/android.hardware.audio.service-aidl.example.rc     (0644, vendor_configs_file)
etc/vintf/manifest/android.hardware.audio.service-aidl.xml  (0644, vendor_configs_file)
etc/primary_audio_policy_configuration.xml                  (0644, vendor_configs_file)
etc/audio_policy_engine_configuration.xml                   (0644, vendor_configs_file)
etc/audio_policy_engine_product_strategies.xml              (同上)
etc/audio_policy_engine_stream_volumes.xml                  (同上)
etc/audio_policy_engine_default_stream_volumes.xml          (同上)
```

## 9. SELinux / baselines

`register_labels()` 把标签登记进 baselines 清单（`vendor.selinux-labels`）：

- 两个 bin -> `u:object_r:hal_audio_default_exec:s0`
- rc / vintf / policy / 引擎配置 xml -> `u:object_r:vendor_configs_file:s0`

## 10. 已知限制

- **强依赖 `hal/android15` 线引擎**（本模块不含驱动源码/aidl/platform/common，也不自带编译
  流水线）：调用前必须已跑 `python3 hal/android15/build/deps.py audio --platform` 备好 AIDL
  与 platform 头快照，且 `hal/android15/common/`、`hal/android15/android.hardware.audio/{src,build.py}`
  就位；env `CDH_HAL_COMMON` 可改 common 位置。
- **`getEngineConfig` 卷组名/`productStrategies` 强约束**（见 §2）：改名或填 productStrategies
  会分别导致音量组塌缩为 Min/Max=0、或小米闭源扩展崩溃 audioserver。
- **host 出声依赖 QEMU 侧 `mixer=off` + `DefaultSystemOutputDevice`**（见 §4）：二者在
  daemon/QEMU，不在本模块；若 host 无声先核对这两处与 host 系统输出设备选择。
- **ABI 运行库研磨需 docker + gold-15 lineage-22.0 super 解包**：本机无 docker 时，可用
  env `CDH_RUNTIME` 指向已研磨的运行库目录（含 `libc++.so / libbinder_ndk.so`）跳过研磨。
- **ConfigImpl 泛化**（见 §2）：卷组为空，音量依赖 `/vendor/etc` 引擎 XML 链；如需机型
  精确卷组，应在 `hal/android15/android.hardware.audio/src/ConfigImpl.cpp` 回填（或参考
  note13-5G-15 版）。
