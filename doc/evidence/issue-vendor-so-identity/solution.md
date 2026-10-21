# Issue: 云机 App 观测到的 vendor .so 加载来源判定（zygote 继承 vs App 自加载）—— 解决方案

- 设备: Xiaomi Redmi Note 13 5G (note13-5G-15)，**真机 root 取证**（`su`，`u:r:magisk:s0`）
- 云机: lineage-22.0 virtio vendor 为底（官方 super 零改动 + vdc patch.img + 内核 getname_flags 重定向）
- 状态: **已定性**（2026-10-09）：真机 59 条已加载 `/vendor/lib64/*.so` 中，**56 条由 zygote64
  预加载后被 fork 继承（COW）**，仅 **3 条**为 App 进程运行时 dlopen。据此**推翻**
  `build/doc/vendor-so-identity.md` §14 v1（内核 uid 分流）的前提。

---

## 1. 问题

方案（`vendor-so-identity.md`）的目标是让云机上 App 进程经 `/proc/self/maps` 观测到的
`/vendor/lib64/**` 已加载 `.so` 集合（**路径名 + 文件字节 md5**）与真机一致。§14 v1 给出的
落地手段是**内核 uid 分流**：在 `kernel/source/fs/namei.c` 的 `cdh_redirect_vendor_path()`
入口加 `current_fsuid() >= 10000` 判定，让 App 进程不走重定向、直读官方 gold `/vendor`。

该手段**隐含一个未经证实的前提**：App maps 里那批 `/vendor/lib64/*.so` 都是**该 App 进程
自己**在运行时 dlopen 的（即"谁解析路径"= App 进程）。若前提不成立，uid 分流就无效——
因为 Linux 进程的映射只能由本进程 mmap、或由父进程 fork 继承；**继承来的映射，其路径早在
父进程（zygote）里就解析完了**，App 改自己的 uid 不会重解析。

问题：**这批 so 到底是 App 自己读的，还是从 zygote 继承的？**

## 2. 根因（判定结论）

真机实测：**绝大多数是 zygote 自身预加载后被 fork 继承**——既不是 App 自加载，也**不是从 init 继承**。

- **不是 init**：`init`(pid 1) 的 maps 里 vendor `.so` = **0**；而 `zygote64` 的 PPID=1，
  唯一祖先就是 init ⇒ 它这 56 条**只能是自己 mmap 的**。
- **zygote 预加载（ABI 对称）**：`zygote64`(uid 0) 与 **32 位 `zygote`** 各自加载 **56** 条，
  且 **basename 集合完全相同（0 差异）** ⇒ 是 zygote 启动时的对称预加载（MIUI 将 MTK/MEOW
  图形栈预加载进 zygote），不是被任何 App 的 GL 调用触发的。
- **App 侧 56 条为继承**：被测 App 的父进程就是 `zygote64`；`webview_zygote`（同为 zygote64 的子进程）
  的 56 条与 zygote64 **完全相同**，`com.android.settings`（从不跑 GL）亦完全相同 ⇒ 纯 fork COW 继承。
- **只有 3 条是运行时 dlopen**：`arm.graphics-V5-ndk.so`、`hw/mapper.mediatek.so`、`libdrm.so`。
  它们既不在 zygote 里，也不是 device-checker 专属——`system_server` 同样多出**一模一样的这 3 条**，
  说明是**进程运行时经 graphics 路径各自 dlopen**（任何用到 graphics 的进程都会）。
- 机制推论：zygote 是 `system_server` 与**所有 App** 的共同父进程；`zygote64` 以 **uid 0**
  解析这些路径，**早于 fork**。因此 `current_fsuid() >= 10000` 这道闸门对那 56 条**完全不生效**：
  云机上它们仍由 zygote（uid 0）解析到 `/mnt/vendor`（lineage/CDH 字节），App 只是继承。

> 这也解释了 `vendor-so-identity.md` §13.1 为什么恰好是 `libc++.so`、`egl/libGLES_mali.so`、
> `egl/libMEOW_{data,gift,qt,trace}.so`、`libOpenCL.so` 这 4~7 条 md5 ✅——它们是 CDH 已**回填进
> patch.img（= 重定向路径）**的 gold 原件，zygote 加载时就已读到 gold 字节。

## 3. 证据（真机 root 实测，2026-10-09）

| 进程 | pid（PPID） | uid | 已加载 vendor `.so` | 说明 |
|---|---|---|---|---|
| `init` | 1（0） | 0 | **0** | 排除"从 init 继承" |
| `zygote64` | 868（1） | 0 | **56** | 自身 mmap 整套 MTK/MEOW 图形栈；被测 App 的父进程 |
| `zygote`（32 位） | 870（1） | 0 | **56** | 与 64 位 zygote **basename 完全相同** → ABI 对称预加载 |
| `webview_zygote` | 2292（868） | webview_zygote | **56** | 与 zygote64 **完全相同** → 纯继承 |
| `system_server` | 1701（868） | system | **59** | 56 继承 + 同 3 条运行时 dlopen |
| `com.android.settings`（从不跑 GL） | 11089 | — | **56** | 与 zygote64 **完全相同（0 条多出）** |
| `dev.checker`（被测 App） | 27625（868） | 10299 | **59** | 56 继承 + 3 条运行时 dlopen |

集合运算（路径集合，`Z`=zygote64、`A`=dev.checker）：

- `Z ⊆ A`，`Z \ A = 0`，**`A \ Z = 3`**：
  `/vendor/lib64/arm.graphics-V5-ndk.so`、`/vendor/lib64/hw/mapper.mediatek.so`、`/vendor/lib64/libdrm.so`
  → 这 3 条为 App 运行时 dlopen，其余 **56 条为继承**。

继承实锤（同一文件在 zygote64 与 dev.checker 的**映射地址区间完全相同**，只有 fork COW 才会如此）：

| so | zygote64 | dev.checker |
|---|---|---|
| `egl/libGLES_mali.so` | `6da702f000-6da7988000` | `6da702f000-6da7988000` |
| `hw/vulkan.mali.so` | `6da6d5d000-6da6d5e000` | `6da6d5d000-6da6d5e000` |
| `libbase.so` | `6da2dc0000-6da2dce000` | `6da2dc0000-6da2dce000` |
| `libgpud.so` | `6da3347000-6da335f000` | `6da3347000-6da335f000` |
| `libutils.so` | `6da6bcf000-6da6bdd000` | `6da6bcf000-6da6bdd000` |

结论链：`init` 无这批库（→ 不是从 init 继承）＋ zygote64 自身持有 56 条（→ zygote 自己 mmap）
＋ 不跑 GL 的 settings 也是同一 56 条（→ 纯继承，与 App 行为无关）＋ 地址区间一致（→ fork COW）。

原始输出与可复现命令见同目录 `log-fail.txt`（问题现场/证伪）与 `log-pass.txt`（判定实验原始输出）。

## 4. 改动

- **本次（2026-10-09）**：仅**修订文档**，未改代码。
  - 重写 `build/doc/vendor-so-identity.md` §14：废弃 v1 的"uid 分流即可对齐 59 条"前提，
    改为"须在 **zygote 解析点** 让名字+字节变 gold"的 v2 路线。
  - 本目录归档本次判定的证据链。
- **代码路线（v2，待启动）**：
  - **R1 回填（推荐先做）**：保持重定向不变，把真机 gold 原件放进 patch.img 的**重定向路径**
    （`/mnt/vendor/lib64/...`）→ zygote 加载即得 gold 字节，**路径名天然 = 真机名**。已有实证：
    §13.1 的 4~7 条 ✅ 就是 CDH 已回填的那几个（`libGLES_mali.so`/`libMEOW_*`/`libOpenCL.so`/`libc++.so`）。
    仍需解决"**名字集合**对齐"：真机 zygote 自动预加载了那 56 个真机 soname，云机 zygote 目前
    加载的是 CDH lineage 名（`libEGL_meow.so`/`libGLESv2_meow.so`/`*_mesa.so` 等）。
  - **R2 zygote 侧注入**：在云机 **zygote** 里按真机 soname 清单 dlopen（而非在 App 进程里），
    一次覆盖全部 App（继承）。⚠ 风险：`zygote64` 同时是 `system_server` 的父进程，system_server 会
    一并继承这些映射——继承本身通常无害（不调用即不初始化），但需评估其被调用的路径。
  - 内核 uid 分流（v1）：**降级为可选**，仅对 `A \ Z` 那 3 条 App 运行时 dlopen 的库有意义。
- 未做：内核改动、patch.img 内容改动、CDH 源码改动。

## 5. 验证

本次"验证"即**判定实验本身**（判定加载来源的方法论，可复现）：

1. 取参照集 `Z` = `zygote64` 的 vendor so 集合（root：`cat /proc/<zygote64>/maps`）；
2. 取被测集 `A` = 被测 App 的 vendor so 集合（root：`cat /proc/<app_pid>/maps`）；
3. `comm` 做双向差集：`A \ Z` = **必为 App 运行时 dlopen**（zygote 从未映射）；
   `Z ⊆ A` 且地址区间相同 = **继承**；
4. 反证：`init`（pid 1）应无该批库；另一个从不跑 GL 的 App 应与 `Z` 完全相同。

判据充分性：`/proc/<pid>/maps` 的 pathname 来自 vma 记录的文件 dentry，**在 mmap 当时**确定；
fork 复制 mm，因此子进程该字段 = 父进程解析结果。地址区间一致可排除"两进程各自 dlopen 到同一地址"。

## 6. 遗留 / 下一步

1. **云机侧无法直接取证**：云机 adb 为 `shell`(2000)、`adb root` 被拒（production build）、无 `su`，
   读不到云机 `zygote64` 的 maps。云机上"继承关系"目前只能由 App 侧 maps 间接推断，
   **需找 root 通道**（web 终端 shell channel / guest daemon PTY）或新增"读云机 zygote maps"的检查。
2. **云机 zygote 的名字集合未测**：真机 zygote 是自动预加载那 56 个真机 soname；
   云机 zygote 加载的是哪一套（lineage/CDH 名）尚未实测——这决定 R1 能否只靠"回填"解决，
   还是必须叠加 R2（注入真机名）。
3. **R1 vs R2 取舍未定**：R1 风险低但需解决名字集合；R2 覆盖面全但有 system_server 连带风险。
4. **gold 回填清单来源**：真机 zygote 56 条（本目录 `log-pass.txt`）+ App 3 条 = 59 条；
   全量 md5 基线可在宿主 `roms/gold_images_OS3.0.9.0.VNQCNXM_15.0/vendor_a/` 直接算。
5. `vendor-so-identity.md` §1/§6/§9/§11 中"59 条 = App 已加载"的表述建议同步补一句
   "其中 56 条系 zygote 继承"，避免后续再按"App 自己加载"做方案。