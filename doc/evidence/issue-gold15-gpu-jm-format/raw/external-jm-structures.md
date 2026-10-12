# 外部权威原文摘录：JM 作业链与描述符（round 7 取回，供离线审计）

> 来源与完整 URL 见 [../../../../gpu-hw-emu-refs.md](../../../../gpu-hw-emu-refs.md) §4。
> 本文件只保留**结论所依赖的原文片段**，避免上游链接失效后无法审计。

## 1. `enum mali_job_type`（老版 Mesa `src/panfrost/include/panfrost-job.h`）

```c
enum mali_job_type {
        JOB_NOT_STARTED	= 0,
        JOB_TYPE_NULL = 1,
        JOB_TYPE_SET_VALUE = 2,
        JOB_TYPE_CACHE_FLUSH = 3,
        JOB_TYPE_COMPUTE = 4,
        JOB_TYPE_VERTEX = 5,
        JOB_TYPE_GEOMETRY = 6,
        JOB_TYPE_TILER = 7,
        JOB_TYPE_FUSED = 8,
        JOB_TYPE_FRAGMENT = 9,
};
```
→ 与 gold-15 侧 kbase `JS<n>_FEATURES` 位号（NULL=1…FRAGMENT=9）**完全一致**；
且项目 patch 16d 实测 Fragment Job 的 `Type` 字段 = **9** ⇒ **三源一致**。

## 2. `struct mali_job_descriptor_header`（同文件）

```c
struct mali_job_descriptor_header {
        u32 exception_status;
        u32 first_incomplete_task;
        u64 fault_pointer;
        u8 job_descriptor_size : 1;
        enum mali_job_type job_type : 7;
        u8 job_barrier : 1;
        u8 unknown_flags : 7;
        u16 job_index;
        u16 job_dependency_index_1;
        u16 job_dependency_index_2;

        union {
                u64 next_job_64;
                u32 next_job_32;
        };
} __attribute__((packed));
```
→ 逐字段对上 gold-15 实测：`Type u7 @ (0x10:1)`、`Next u64 @ 0x18`。

## 3. `struct mali_payload_fragment`（同文件）——Fragment job 的 payload（0x20 起）

```c
struct mali_payload_fragment {
        u32 min_tile_coord;
        u32 max_tile_coord;
        mali_ptr framebuffer;
} __attribute__((packed));
```
→ gold-15 实测的 `FBD 指针 u64 @ 0x28` = 本结构的 `framebuffer`；`@0x20` 实为 `min_tile_coord`（项目当时记作 "Payload"）。

## 4. scoreboard 语义（`src/panfrost/lib/pan_jc.h` 注释原文摘录）

```
 * In between a full batch and a single Mali job is the "job chain", a series
 * of Mali jobs together forming a linked list. Within the job chain, each Mali
 * job can set (up to) two dependencies on other earlier jobs in the chain.
 * This dependency graph forms a scoreboard.
 ...
 * - Jobs linking via next_job has no bearing on order of execution, rather it
 *   just establishes the linked list of jobs, EXCEPT:
 * - A job's dependencies must appear earlier in the linked list (job chain).
 ...
 * - The tiler is not thread-safe; this dependency prevents race conditions
 *   between two different jobs trying to write to the tiler outputs at the
 *   same time.
 * - Internally, jobs are scoreboarded; the next job fields just form a linked
 *   list to allow the jobs to be read in; the execution order is from
 *   resolving the dependency fields instead.
```

作业类型语义（同文件注释原文）：

```
 *  - WRITE_VALUE: generic write primitive, used to zero tiler field
 *  - VERTEX: runs a vertex shader
 *  - TILER: runs tiling and sets up a fragment shader
 *  - FRAGMENT: runs fragment shaders and writes out
 *  - COMPUTE: runs a compute shader
 *  - FUSED: vertex+tiler fused together, implicit intradependency (Bifrost)
 *  - GEOMETRY: runs a geometry shader (unimplemented)
 *  - CACHE_FLUSH: unseen in the wild, theoretically cache flush
```

Valhall（`PAN_ARCH >= 9`）额外"使用 tiling"的类型：

```c
   if (type == MALI_JOB_TYPE_MALLOC_VERTEX ||
       type == MALI_JOB_TYPE_FULLSCREEN)
      return true;
```

## 5. 24 位程序计数器 / 2²⁴ shader 池（老版 `panfrost-job.h`，注释原文摘录）

```
         * high 32 bits are implicitly the same as the original shader.
         * According to the kernel driver, the program counter for
         * shaders is actually only 24 bits, so shaders cannot cross
         * the 2^24-byte boundary, and neither can the blend shader.
         * The blob handles this by allocating a 2^24 byte pool for
         * shaders, and making sure that any blend shaders are stored
         * in the same pool as the original shader. The kernel will
         * make sure this allocation is aligned to 2^24 bytes.
```
→ 印证宿主 panfrost 的"可执行 BO 不得跨 16 MB"（`panfrost_gem.c:129-140`）与路线 C 的着色器放置约束。

## 6. 分块几何（同文件）

```c
#define MALI_TILE_SHIFT 4
#define MALI_TILE_LENGTH (1 << MALI_TILE_SHIFT)     /* 16x16 */
#define MALI_X_COORD_MASK 0x00000FFF
#define MALI_Y_COORD_MASK 0x0FFF0000
```

## 7. 版本差异（必须记住的一条）

老版 `enum mali_mfbd_block_format`（2 位）= `TILED=0, UNKNOWN=1, LINEAR=2, AFBC=3`；
而 gold-15 侧实测（来自 Mesa **v9** genxml 的 4 位字段）= `0=No Write, 1=Tiled, 2=Linear, 12=AFBC, 13=AFBC Tiled`。
⇒ **block format 编码随版本变化，落地时必须用 v9 的值**（权威：`src/panfrost/genxml/v9.xml`）。
