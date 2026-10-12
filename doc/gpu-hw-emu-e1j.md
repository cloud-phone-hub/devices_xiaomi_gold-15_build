# gold-15 路线 E1/J 实施方案（出画保底）与组合验收矩阵

> 定位：[gpu-hw-emu.md](gpu-hw-emu.md) 家族 II（API 重映射 / 换栈）的**落地方案**。
> 核心发现：本仓库**已有一套投产的路线 J 实现**——note13-5G-15 的 `android.hardware.gpu`
> （[config/gpu.json](../../../note13-5G-15/build/vendor/source/android.hardware.gpu/config/gpu.json)），
> 它把 Mesa/venus 实际渲染与 Mali 身份对齐解耦；**gold-15 尚未移植**。本文件给出移植清单与冲突点。

---

## 0. 结论速览

| 结论 | 依据 |
|---|---|
| **路线 J 不是设想，是已投产实现** | note13-5G-15 `android.hardware.gpu`：EGL 三件套 interposer + Vulkan interposer（包装 venus）+ 扫描面回填 + 属性/标签，且**不改任何基座二进制** |
| **gold-15 未移植** | `gold-15/build/vendor/source/` 只有 `android.hardware.gatekeeper`、`android.hardware.keymint`、`daemon` |
| **gold-15 的图形属性已"恰好就位一半"** | 官方 vendor `build.prop` 实测已是 `ro.hardware.egl=meow`、`ro.hardware.vulkan=mali`、`ro.vulkan…`、`ro.hwui.use_vulkan=true` → **interposer 的 suffix 与属性对齐，无需改属性** |
| **唯一冲突点 = 显示/分配面** | gold-15 官方 vendor 的 `ro.hardware.hwcomposer=mtk_common` + MTK gralloc（AFBC/AFRC）/MTK composer；而 note13 用 `hwcomposer=drm` + minigbm。二者**不能双实例共存**（VINTF + 分配器语义）→ 必须二选一 |
| **可"只新增文件"的部分** | EGL/GLES/Vulkan 三条 interposer + Mesa/venus 基础库 + 扫描面回填（**添加**文件；`ro.*` 已是 meow/mali） |
| **不可避免的 vendor 触碰** | 若要把 composer 切到 `drm`：**只能改 vendor `build.prop`**（`ro.*` 是 set-once，init 的 `setprop` 覆盖不了——note13 已用实测写明） |
| **隐藏收益** | 走 E1/J 后，现行 2127 行补丁脚本里的 **Mali/virtio-MTK 补丁整类失效**（见 §4） |

---

## 1. 现状取证（本轮实测）

### 1.1 gold-15：官方 MTK 图形栈 + 属性

`vendor_a.img`（官方，EROFS）`/build.prop` 实测（提取：`fsck.erofs --extract=... --path=/build.prop`）：

| 属性 | gold-15 官方值 | note13（路线 J 目标值） |
|---|---|---|
| `ro.hardware.egl` | **`meow`** | **`meow`** ✅ 已一致 |
| `ro.hardware.vulkan` | **`mali`** | **`mali`** ✅ 已一致 |
| `ro.hardware.hwcomposer` | **`mtk_common`** | **`drm`** ❌ 冲突 |
| `ro.hwui.use_vulkan` | `true` | （note13 未设） |
| `ro.opengles.version` | `196610` | `196610` ✅ |
| ARM gralloc AFBC/AFRC 开关 | `ro.vendor.arm.gralloc.*` 全量存在（含 `afrc_rgba/luma/chroma`、`no_afbc_*`） | 同（note13 baselines 内有同组值） |
| `ro.vendor.arm.egl.configs.*` | 存在 | 同组（note13 baselines 内 30+ 条） |

→ **`egl=meow`/`vulkan=mali` 两个"开关"天然与 note13 的 interposer suffix 对齐**——这是 gold-15 移植路线 J 的最大便利。

### 1.2 lineage 底包的图形栈（E1 要带入的文件）

从 vendor 树差异（[out/tmp/vendor_tree_diff.txt](../../../../../out/tmp/vendor_tree_diff.txt)：官方 5992 文件 / lineage 273 文件 / **仅 lineage 112**）实测，lineage-only 的图形件：

| 类别 | 文件 |
|---|---|
| EGL/GLES | `lib64/egl/libEGL_mesa.so`、`lib64/egl/libGLESv1_CM_mesa.so`、`lib64/egl/libGLESv2_mesa.so`、`lib64/libgallium_dri.so`、`lib64/libglapi.so` |
| Vulkan | `lib64/hw/vulkan.virtio.so`（venus）、`lib64/hw/vulkan.pastel.so` |
| 分配器 | `lib64/hw/gralloc.minigbm.so`、`lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm.so`、`lib64/hw/mapper.minigbm.so`、`lib64/libminigbm_gralloc.so`、`…4_utils.so`、`bin/hw/android.hardware.graphics.allocator-service.minigbm`、`etc/init/allocator.rc`、`etc/vintf/manifest/{allocator,mapper.minigbm,android.hardware.graphics.mapper@4.0}.xml` |
| HWC | `lib64/hw/hwcomposer.drm.so`、`lib64/libhwc2on1adapter.so`、`lib64/libhwc2onfbadapter.so`、`bin/hw/android.hardware.graphics.composer@2.4-service`、`lib64/android.hardware.graphics.composer@2.{1,2,3,4}*.so`、`etc/init/android.hardware.graphics.composer@2.4-service.rc` |
| 其它 | `bin/virtgpu_detect`、`apex/com.android.hardware.drm.clearkey.apex` |
| 标签差异 | `lib64/android.hardware.graphics.allocator@2/3/4.0.so`、`lib64/libui.so`（官方 `vendor_file` vs lineage `same_process_hal_file`） |

### 1.3 note13-5G-15 的 `android.hardware.gpu`：路线 J 的投产实现

| 机制 | 内容 |
|---|---|
| 出发点 | `"approach": "interposer"`，**不修改任何基座二进制** |
| EGL | EGL Loader 先试 `libGLES_<suffix>.so`（单库全导出），失败才退回 `libEGL_/libGLESv1_CM_/libGLESv2_<suffix>.so` 三件套；本模块**只产出三件套** → 必然走退回路径 → 与基座 Mesa 三件一一对应 |
| 身份覆写 | `glGetString`/`glGetStringi`/`glGetIntegerv` 覆写为真机 Mali-G57 值（`ARM` / `Mali-G57 MC2` / `OpenGL ES 3.2 v1.r49p1-…`）；**GLSL 版本故意不对齐**（曾覆写成 3.20 → Skia 全 shader 编译失败、App 内容区空白，已记录为事故） |
| 扩展串 | 只做减法：剥掉 `GL_MESA_`/`GL_APPLE_`/`GL_ANGLE_` 三类宿主/驱动指纹；**绝不添加** Mali 独有扩展 |
| Vulkan | `lib64/hw/vulkan.mali.so` = interposer，**只包装 `HMI`（`vkGetInstanceProcAddr`）** 并覆写 `vkGetPhysicalDeviceProperties*` 的 `deviceName`，其余全部转发给基座 **`vulkan.virtio.so`（venus）** |
| 扫描面 | `scan-libs` 从官方 gold vendor **逐字节回填**真机 GPU 库（含 sha256），**只为 stat/access/dlopen 探测** |
| **严禁回填三条** | ① `lib64/egl/libGLES_meow.so`（= MTK 真驱动单库；一旦在，Loader 会命中它并跳过三件套 → 无 `/dev/mali0` → **整机无图形**）；② `lib64/hw/vulkan.mali.so`（会覆盖 interposer）；③ `gralloc*`（与 minigbm 不兼容） |
| 属性 | `ro.hardware.egl=meow`、`ro.hardware.vulkan=mali`；note13 另有 `ro.hardware.hwcomposer=drm`、`ro.opengles.version=196610`、`debug.hwui.initialize_gl_always=1`、ARM gralloc/EGL config 组 |
| 标签 | interposer 与扫描库统一 `u:object_r:same_process_hal_file:s0` |

### 1.4 gold-15 是否已移植

`gold-15/build/vendor/source/` = `android.hardware.{gatekeeper,keymint}` + `daemon` → **未移植 `android.hardware.gpu`**；
`baselines/*.json` 的 `vendor` 段也没有图形 prop-sets。→ **移植是本方案的主要工作量。**

---

## 2. 路线 J 移植方案（推荐：以"新增文件 + 身份对齐"为主）

### 2.1 需要新增的文件

| # | 文件 | 来源 | 说明 |
|---|---|---|---|
| 1 | `lib64/egl/libEGL_meow.so`、`libEGLv1_CM_meow.so`、`libGLESv2_meow.so` | note13 `android.hardware.gpu` 产出（interposer） | 转发 + 身份覆写 |
| 2 | `lib64/hw/vulkan.mali.so` | 同上（Vulkan interposer） | 包装 `vulkan.virtio.so` |
| 3 | `lib64/egl/libEGL_mesa.so`、`libGLESv1_CM_mesa.so`、`libGLESv2_mesa.so`、`lib64/libgallium_dri.so`、`lib64/libglapi.so` | **lineage vendor**（基座库，interposer 的 `dlopen` 目标） | gold-15 官方 vendor 没有 Mesa |
| 4 | `lib64/hw/vulkan.virtio.so`（+ `vulkan.pastel.so` 视需要） | **lineage vendor** | venus：Vulkan 落宿主 GPU |
| 5 | 扫描面回填：`lib64/egl/libGLES_mali.so`、`libMEOW_{data,gift,qt,trace}.so`、`lib64/libOpenCL.so` | **官方 gold vendor 逐字节** | 只为探测面；**严禁回填 `libGLES_meow.so`** |

### 2.2 必须同时做的三件事（否则整机无图形 / 冲突）

| 事 | 原因 |
|---|---|
| **不要**回填 `lib64/egl/libGLES_meow.so` | EGL Loader 的单库优先路径会命中 MTK 真驱动（无 `/dev/mali0` → 无图形） |
| **不要**回填 `lib64/hw/vulkan.mali.so` | 会覆盖我们的 Vulkan interposer |
| SELinux 标签：interposer/扫描库 → `same_process_hal_file` | 否则 `same_process_hal_file` 域的进程无法 `dlopen`（App/SF 在同进程加载 EGL/GLES） |

### 2.3 显示与分配面的**硬冲突**（本方案的关键决策点）

| 面 | gold-15 官方（现状） | note13/E1 目标 | 能否共存 |
|---|---|---|---|
| EGL/GLES | 厂商 Mali（需 `/dev/mali0`） | Mesa（interposer 转发） | ✅ **可共存**（interposer 只改 EGL 解析路径，厂商文件仍在位） |
| Vulkan | `vulkan.mali.so`（厂商 Mali Vulkan，需 `/dev/mali0`） | 我们的 interposer → venus | ✅ 可共存（以 interposer 占位，厂商原件作扫描面） |
| allocator | MTK gralloc（AFBC/AFRC） | minigbm | ❌ **不可共存**：Vulkan/GL 的 buffer 必须被宿主 venus/virgl 识别，AFBC/AFRC 压缩布局宿主无法直接消费 |
| composer | `hwcomposer.mt6833.so`（MTK，`hwcomposer=mtk_common`） | `hwcomposer.drm.so`（`hwcomposer=drm`） | ❌ **不可共存**：两个 HWComposer HAL 实例会 VINTF 冲突；且显示语义（MTK DDP vs virtio-gpu）互斥 |
| 属性 `ro.hardware.hwcomposer` | `mtk_common`（vendor build.prop） | `drm` | ❌ `ro.*` set-once：**init 的 `setprop` 覆盖不了 build.prop**（note13 实测记录）→ 只能改 vendor `build.prop` |

**由此得出两条可选落地形态**：

| 形态 | 做法 | "改 vendor 文件"面 | 出画 |
|---|---|---|---|
| **J-lite（只解 EGL/Vulkan）** | 只加 interposer + Mesa/venus 库 + 扫描面；**保留 MTK 分配器/composer** | **零**（纯新增） | ❌ **大概率不出画**——渲染结果要经 MTK 分配器（AFBC/AFRC）与 MTK composer，宿主侧 venus/virgl 与 Mesa 都不认这套格式 |
| **J/E1 完整（推荐）** | 加 interposer + Mesa/venus 库 **且**把分配器/composer 换成 lineage 版（minigbm + `hwcomposer.drm`），并改 `build.prop` 的 `ro.hardware.hwcomposer=drm` | **1 个属性文件**（`/vendor/build.prop`）+ 需停用 MTK composer 的 VINTF/init | ✅ **等价于 note13-5G-15 的既有出画形态**（可直接对齐该设备验收） |

> **诚实口径**：完整形态**不是"零 vendor 改动"**——它必须改 `/vendor/build.prop` 一行属性，并处理 MTK composer 的 VINTF/init 停用；
> 但**厂商 .so 文件本身（名字与 md5）一个都不动**（这正是 [note13 vendor-so-identity](../../../note13-5G-15/build/doc/vendor-so-identity.md) 约束关心的面）。

---

## 3. 路线 E1 变体（把 lineage 图形栈整体带入）

若接受"显示栈整体替换"，则不需要 interposer（EGL 直接走 Mesa）：

1. 把 §1.2 的 lineage 图形件**整体带入** gold-15 vendor（含 gralloc/minigbm/hwcomposer.drm/composer service/VINTF manifest/init rc）；
2. `ro.hardware.hwcomposer=drm`（改 build.prop）；`ro.hardware.egl`/`vulkan` 保持 `meow`/`mali` 也无妨（没有 `libEGL_meow.so` 时 Loader 退回 `libEGL.so`；但若走 E1 建议直接对齐 lineage：清空 egl/vulkan 属性或设为 `mesa`/`virtio`）；
3. 停用/移除 MTK 的 allocator/composer service 与其 VINTF manifest（避免双实例）；
4. 与 J 完整形态的差异：**少了 interposer 的身份覆写** → App 看到的 GL 串是 Mesa/virgl 真值（身份对齐需另做，note13 正是为这个才用 interposer）。

**E1 与 J 完整形态的关系**：J = E1 + interposer（身份/探测面对齐）。若验收含"GL 串/扩展面像真机"，选 J；若只看"能出画"，E1 更简单。

---

## 4. 内核侧的连带影响（E1/J 的隐藏收益）

现行 `wire-kbase.sh`（2127 行、6 组 kbase 补丁 + 一批 virtio-gpu/drm 补丁）是为"**厂商 Mali/composer 在 virtio-gpu 上活着**"服务的。
走 E1/J 后这些补丁**全部失效或不再需要**：

| 补丁组 | 在 E1/J 下 | 原因 |
|---|---|---|
| kbase 组（1/2/3/4/12/12b/12c/16/16b/16c/27*） | **不需要** | 不再使用 Mali DDK（`/dev/mali0` 无消费者） |
| `cdh_mali_plat` / `ged_stub` / `mtk-props` 模块 | **不需要加载** | MTK HWC/GED 不再参与 |
| virtio-gpu MTK 形态（连接器 DSI、49 私有属性、`nr=0x43` 分流、dumb bpp、plane format、fb_modifiers、MTK ioctl stub、master 授权…） | **不需要** | `hwcomposer.drm.so` 走标准 DRM/KMS 语义（Cuttlefish 风格），不需要 MTK 私有语义 |
| vblank（补丁 14）、scanout 填色桥（18） | **不需要** | 标准 DRM + readback 路径已通（[Display.md](../../../../../core/host/daemon/Display.md) §2.5） |

→ **E1/J 把"内核侧 2127 行补丁"变成"内核对齐 lineage 底包"**（这正是 note13-5G-15 的形态）。
> 注意：这些是**方案描述**，按用户指令本轮不改任何代码。

---

## 5. 组合验收矩阵

| 组合 | 客机图形栈 | 内核补丁 | 出画判据 | 探测面判据 | 工期 | 风险 |
|---|---|---|---|---|---|---|
| **现状（K/V0）** | 厂商 Mali（虚拟化） | 2127 行 | ❌ B2 v1 静态灰 | ✅ 厂商 so 在位 | 已投入 | 高（不出画） |
| **J-lite** | interposer + Mesa/venus，分配器仍 MTK | 2127 行（仍需） | ❌ 预期不出画（§2.3） | ✅ + GL 串为 Mali | 小 | 中 |
| **J 完整（推荐）** | interposer + Mesa/venus + minigbm + drm composer | **可回退到 lineage 对齐** | ✅ 对齐 note13（GL+Venus 出画） | ✅ GL 串/扩展已对齐；厂商文件在位 | 中（移植 note13 模块 + 换分配器/composer + 改 1 行属性） | 中 |
| **E1** | Mesa/venus + minigbm + drm composer（无 interposer） | 同上 | ✅ 出画 | ⚠️ GL 串为 Mesa 真值 | 中 | 低（少一层 interposer） |
| **A/B（硬件仿真）** | 厂商 Mali（不改） | 仅保留 ABI 补丁 | T1 ✅ / T2 部分 / T3 ❌ | ✅ | 大 | 中（T1/T2）/高（T3） |
| **C（宿主换真 Mali）** | 厂商 Mali（不改） | 同上 | ✅（同族 ISA） | ✅ | 大 | 中 |

---

## 6. 风险与回退

| # | 风险 | 缓解/回退 |
|---|---|---|
| 1 | 分配器/composer 替换引发其它子系统（相机/视频/DRM）回归 | 先只换 allocator+composer+HAL 服务，逐项回归（对比 [note13 出画验收](../../../note13-5G-15/build/doc/)） |
| 2 | `build.prop` 改动与官方基线冲突（baselines 校验） | 只改一行属性并登记到 `baselines/*.json`（沿用现有 prop-sets 机制） |
| 3 | VINTF 双 composer 实例 | 用 prop 选型 + 不启动 MTK composer service（或在 baselines 里登记其停用） |
| 4 | interposer 与 Mesa 版本耦合（dlopen 基座 soname） | 与 note13 同源 lineage 底包，soname 一致 |
| 5 | GLSL 版本覆写事故重演 | **沿用 note13 的决定：不覆写 GLSL 版本**（gpu.json 已写明事故） |
| 6 | 回退 | 全程只新增文件 + 1 行属性；回退 = 撤掉新增 + 恢复属性（MTK 栈完好） |

---

## 7. 建议

1. **移植 note13 的 `android.hardware.gpu`**（EGL/VK interposer + 扫描面 + 标签），复用其"严禁回填三条"与"GLSL 不覆写"的既有教训；
2. **同时带入 lineage 的 minigbm 分配器与 `hwcomposer.drm`**，并改 `/vendor/build.prop` 的 `ro.hardware.hwcomposer=drm`（唯一 unavoidable 的 vendor 文件触碰，需记入 baselines）；
3. **内核侧暂不动**（当前补丁虽冗余但不阻塞）；待 J 完整形态出画后，再按 §4 逐步回退 Mali/virtio-MTK 补丁；
4. 验收直接**对齐 note13-5G-15**（同硬件、同 lineage 底包、已知出画），差异点仅"gold-15 的官方 vendor 内容更多"。

---

## 附录 · 证据索引

| 事实 | 来源 |
|---|---|
| 官方 gold vendor 图形属性 | `vendor_a.img:/build.prop`（EROFS，本轮提取；`ro.hardware.egl=meow`、`hwcomposer=mtk_common`、`vulkan=mali`、`gralloc.afrc_*`） |
| lineage-only 图形文件清单（112 项中的图形子集） | [out/tmp/vendor_tree_diff.txt](../../../../../out/tmp/vendor_tree_diff.txt) + `vendor_tree_diff_files.txt`（`# only_lineage` 段） |
| 路线 J 投产实现 | [note13 `android.hardware.gpu/config/gpu.json`](../../../note13-5G-15/build/vendor/source/android.hardware.gpu/config/gpu.json)（`approach=interposer`、`scan-libs`、`严禁回填三条`、`vulkan` interposer、`properties`） |
| 属性 set-once 覆盖顺序 | 同上 `properties-note`（build.prop 早于基座 `init.virtio.rc` 的 `setprop … mesa` → build.prop 胜出） |
| gold-15 未移植 | `gold-15/build/vendor/source/`（仅 gatekeeper/keymint/daemon） |
| 宿主 venus/virgl 通路与出画判据 | [Display.md](../../../../../core/host/daemon/Display.md) §2.1–2.5（实例 39：1080x2400、46fps） |
| 现行内核补丁清单 | [gpu-hw-emu.md](gpu-hw-emu.md) §2.8、`kernel/modules/mali_vgpu/wire-kbase.sh` |
