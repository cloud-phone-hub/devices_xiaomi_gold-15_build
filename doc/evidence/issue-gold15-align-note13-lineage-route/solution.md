# issue-gold15-align-note13-lineage-route：gold-15 整树对齐 note13 构建过程并首启成功

## 1. 问题

需求：把 `devices/xiaomi/gold-15/build` 的构建过程**整树对齐** `note13-5G-15/build`
（用户选定"整树覆盖·真·对齐 note13"），**但 baselines 例外**——用 gold 自己的
build-id 名 `gold_global_images_OS3.0.5.0.VNQMIXM_20260811.0000.00_15.0_global_92861aa8b1.json`
**重新生成**（保留 gold 设备身份）。验收目标：**能正常启动**。

背景：note13-5G 与 gold 是同一物理机（Redmi Note 13 5G，代号 gold / MTK MT6833），
仅 ROM 变体不同（gold=global `OS3.0.5.0.VNQMIXM`，note13=CN `OS3.0.9.0.VNQCNXM`）。
gold 原走"官方 EROFS vendor 原地污染 + 显示栈换装"路线；note13 走"lineage ext4
基座 + 薄集成 HAL 重编进 staging"路线。对齐=让 gold 改用 note13 路线。

## 2. 根因（对齐后为何不能直接起）

三处"设备身份 vs 构建过程"耦合点决定必须重指/重生，否则 gold 起不来：

1. **buildenv 设备身份**：两树 buildenv 架构相同（CDH_*/manifest 单源），仅 3 处字面量差异
   —— docstring、`VERSION_DIR`（note13-5g↔gold-15）、`_ROM_VER_RE`（note13 只匹配
   `gold_images`，匹配不了 gold 的 `gold_global_images`）。整树覆盖会把 gold 认成 note13、
   产物落错 `out/device/note13-5g` 目录。
2. **基线路线切换**：gold 原基线 `vendor.build-prop-sets` 仅 2 条（egl/vulkan mesa）、
   selinux-labels 161 条——均为"官方 vendor 原地保留"路线量测。lineage 基座的 vendor 是
   **通用 lineage vendor**，需**全量属性集**（~700 条）改写回 gold 身份，且注入文件集变了
   （note13 模块）→ 需 183 条标签。稀疏旧基线会让 vendor 残留 lineage 通用身份 + inject
   标签缺失即报错退出。
3. **官方 vendor 解包目录**：note13 路线的 gpu/gold_props 模块从
   `ROM_ROOT_DIR/vendor_a/`（官方 EROFS vendor 解包树）读文件（6 个 GPU 库 + etc/selinux
   的 property_contexts/sepolicy.cil）。该目录**不在 unpack-roms 产物内**（流水线外一次性
   前置），gold 从未制备 → gpu 报"官方 vendor 解包目录缺失"。

## 3. 证据

见同目录 `log-fail.txt`（对齐过程中逐个暴露的卡点）与 `log-pass.txt`（首启成功指标）。
关键：guest `ro.vendor.build.fingerprint = Redmi/vnd_gold/gold:15/AP3A.240905.015.A2/OS3.0.5.0.VNQMIXM:user/release-keys`
——global 身份串来自重生成基线的 build-prop-sets，证明基线在实机生效。

## 4. 改动

1. **整树覆盖**：`rsync -a --delete` note13/build → gold/build，**排除 `.git` 与 `baselines`**
   （保 gold 仓 + gold 基线目录）。gold 原 base_fs/display/remove/vintf/ueventd 与
   build-super*/fstab_cloud/blankfmt 等旧路线模块随之删除；带入 note13 的 pack_super*/build_odm
   + gold_props/gold_selinux/prune/selinux + 15 个 source HAL 模块 + note13 main.py（9 阶段含
   super 骨架 no-op）。
2. **重指 gold 身份**（`scripts/buildenv.py` 3 处 + `manifest.json`）：docstring→gold-15、
   `VERSION_DIR` 字面量→`gold-15`、`_ROM_VER_RE`→`^(gold(?:_[a-z]+)?_images_OS…)`（允许区域
   变体）；manifest 换回 gold schema2（build-id=gold_global_images_OS3.0.5.0…）。全树 grep 确认
   代码/配置无 note13/cn 身份硬编码泄漏（值均由 manifest→build-id→baselines 参数化）。
3. **重生成 gold 基线**（`baselines/<gold-build-id>.json`）：以 note13 基线为模板（700 条
   build-prop-sets + 183 selinux-labels，匹配 lineage 路线/现模块），把 6 处版本身份串覆盖为
   gold-global 权威实测值（从 gold EROFS vendor_a.img build.prop 用宿主 `dump.erofs --cat` 抽取：
   name=vnd_gold、model=gold、fingerprint/incremental/date/date.utc），rom/lineage/version-description
   保留 gold 实测。生成器：`.backup/gen_gold_baseline.py`。6 个 GPU 库 sha256 与 note13 契约跨
   CN/global 完全一致，无需改 gpu.json。
4. **制备官方解包目录**（流水线外前置）：`dump.erofs --cat` 从 gold `vendor_a.img` 抽出
   `ROM_ROOT_DIR/vendor_a/`：6 个 GPU 库（lib64/egl/* + lib64/libOpenCL.so）+ etc/selinux 15 文件。
5. **清僵尸件 + 删多余模块**：删 gold 独有的 `vendor/source/android.hardware.gatekeeper`（note13
   无此模块，基线无其标签）；清空 `out/dist/vendor` staging 与 `out/tmp`（旧路线残留的
   gatekeeper/keymint `.mitee` 僵尸二进制会让 inject 标签缺失报错）。清后源模块集与 note13 一致。

## 5. 验证

`just device run 0`（gold run-list 索引 0）→ 实例 12 / adb=5911：

- `sys.boot_completed=1`；`getprop | grep restarting` 空（无重启循环）；
- `init.svc.{audioserver,surfaceflinger,logd,vendor.keymint-default}=running`；
- `init.svc.{vendor.audio-hal-aidl,vendor.audio-effect-hal-aidl}=running`（phase-1 effect 声明修复随资产继承）；
- audio core/effect/parameter_parser 进程与 surfaceflinger 均存活；
- `ro.vendor.build.fingerprint` 为 gold-global（基线生效）；`getenforce`=Permissive（pioneer 设计）。

## 6. 遗留 / 后续

1. **audio 接口 lshal 计数 / c2 服务**：本轮 lshal audio/c2 未列（该镜像 lshal 口径），但 init.svc
   与进程在；如需接口级验收需按 note13 那样补 lshal/vintf 明细。
2. **gpu.json / sepolicy.py 里的 `note13-5G-15` 描述性元数据**（`$schema`/`device`/marker 串）未改：
   同机型、不影响构建与运行；如需彻底 gold 化文案可后续统一替换（改动面含 marker 幂等判定，慎改）。
3. **super 阶段**：note13 `pack_super.py` 仍是骨架 no-op；gold 沿用（pack-roms 直写官方 super），
   与预期一致，非缺陷。
4. **蜂窝数据链路/scrcpy 等端到端**：与 note13 同源，未在本轮重复验收。
