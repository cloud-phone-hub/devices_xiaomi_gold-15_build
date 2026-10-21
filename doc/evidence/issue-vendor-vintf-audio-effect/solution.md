# Issue: audioserver SIGSEGV 循环（AudioFlinger::onFirstRef+704 空指针）—— vendor VINTF 缺 audio.effect/IFactory 声明

- 设备: Xiaomi Redmi Note 13 5G (note13-5G-15)
- 宿主: macOS (darwin, QEMU + hvf)
- 状态: **已打通**（2026-10-20，实例 note13-15/3）：audioserver 开机后零重启零崩溃，
  `sys.boot_completed=1`；五个 audio 接口（IModule×3 + IConfig + IFactory）全注册。
- 触发背景: 薄集成层改造后 audio 模块 vintf manifest 沿用 gold 资产，gold 那份
  **只声明 core、未声明 effect**；note13 ROM 的小米定制 `FactoryHal` 在
  `AudioFlinger::onFirstRef` 中解引用 effect 查询结果，缺失即崩。

---

## 1. 问题现象

实例 2（c2 seclabel 修复后）boot_completed 仍不置位，audioserver 约每 5s 崩一次：

```
E FactoryHal: Found no HAL version, main(Device) AudioHalVersion{type: AIDL, major: 1, minor: 0} null!
E FactoryHal: Found no HAL version, main(Effect) null AudioHalVersion{type: AIDL, major: 1, minor: 0}!
F libc    : Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0 in tid 595 (audioserver)
backtrace: #00 /system/bin/audioserver (android::AudioFlinger::onFirstRef()+704)
```

同时 HAL 进程侧一切正常：`cdh_audio: registered android.hardware.audio.effect.IFactory/default`
每次都在打印，`service list` 也能看到接口——**服务活着、注册成功，framework 却查不到**。

## 2. 根因

排查曾一度指向「AIDL hash 不匹配」（hal 引擎 audio core/effect 用 v2 快照、旧配方记录
v3 字样），**逐项取证后排除**：

| 比对项 | HAL 二进制（strings 提取） | ROM 侧（guest /system） | 结论 |
|---|---|---|---|
| IModule hash | `003735b3…`（core/2/.hash） | `android.hardware.audio.core-V2-ndk.so` 内同值 | 一致 |
| IEffect hash | `54d5a2e1…`（effect/2/.hash） | `android.hardware.audio.effect-V2-ndk.so` 内同值 | 一致 |
| IModule 版本协商 | `getInterfaceVersion=2` | `IModule/default: true`（FactoryHal 日志） | 通过 |

真正根因：小米定制 `FactoryHal::hasAidlHalService` 先查 **VINTF 声明面**；
我们的 `android.hardware.audio.service-aidl.xml` 只有 core 四个 fqname，
**没有 `android.hardware.audio.effect`/`IFactory/default`** → effect 查询返回 false →
`main(Effect) null` → 小米 `EffectDapController` 路径对 null 效果工厂直接解引用 → SIGSEGV。
（core 侧 Device null 是 effect 初始化失败链上的连带日志，非独立故障。）

## 3. 证据

`log-fail.txt`：修复前 FactoryHal/AudioFlinger/DEBUG 关键行 +
修复前 staging vintf 文件内容（仅 core 声明）。

排除 hash 假设的过程记录见 §2 表格（数据取自实例 2 运行时
`adb shell strings` 与本机 `out/dist/vendor/bin/hw/*` 对比）。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `build/vendor/source/android.hardware.audio/vendor/etc/vintf/manifest/android.hardware.audio.service-aidl.xml` | 追加 `<hal format="aidl"><name>android.hardware.audio.effect</name><version>2</version><fqname>IFactory/default</fqname></hal>` |

版本取 **2** 依据：二进制嵌入 hash 即 effect v2（`54d5a2e1…`），且 ROM
`/system/etc/vintf/compatibility_matrix.202404.xml` 声明 `IFactory` 兼容区间
`<version>1-2</version>`，v2 必过 VINTF 校验（真机 dump 见过 v3 声明，属旧基线，不采用）。

随后 `python3 build/main.py --from vendor` 重打 vendor→vendor_dlkm→patch→super→pack。

## 5. 验证

`log-pass.txt` 摘要（实例 3）：

```
[sys.boot_completed]: [1]
[init.svc.audioserver]: [running]（pid 593，开机后未重启）
[init.svc.vendor.audio-hal-aidl]: [running]
[init.svc.vendor.audio-effect-hal-aidl]: [running]
service list: IModule/default、/primary、/r_submix、IConfig/default、IFactory/default 全在
logcat -b crash 中 audioserver/audio 服务崩溃计数 = 0
getprop 全表无 restarting 服务
```

## 6. 遗留

- **gold-15 同源缺陷**：gold 的 audio vintf 资产同样缺 effect 声明，若 gold guest
  出现相同 audioserver 崩环，应同步补上（本次仅改 note13，未动 gold）。
- 其余 AIDL HAL 的「framework 期望接口 ↔ vendor manifest 声明面」一致性尚未系统
  抽查（camera/gnss 等本次运行面均正常，暂缓）。
- AIDL hash 排查方法（strings 40hex 提取对比 ROM ndk 库）可复用，建议沉淀到
  设备文档的排障手册。
