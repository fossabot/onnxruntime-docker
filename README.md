# onnxruntime Docker 镜像（Alpine）

把 onnxruntime 与它依赖的 numpy 打成 musllinux wheel 分发，同时提供装好的可运行镜像。
不产出 apk 包。

| 标签 | 内容 | 用途 |
|---|---|---|
| `1.24.4-wheelhouse` | 只有 `/wheels`：onnxruntime 1.24.4 + numpy 2.4.6 两个 wheel + `SHA256SUMS` | 上层 `COPY --from` 取 wheel |
| `1.24.4` | 可运行镜像：alpine 3.24 + Python 3.14 + 上述两个包 | `FROM` 直接用 |
| `wheelhouse` / `latest` | 上面两者的滚动标签 | 跟最新 |

- Docker Hub：<https://hub.docker.com/r/a76yyyy/onnxruntime>
- 源码：[qd-today/onnxruntime-docker](https://github.com/qd-today/onnxruntime-docker)

## 快速开始

取 wheel 自己组装。wheelhouse 里有 PEP 503 索引，`requirements.txt` 照常写依赖即可——
索引里有的从本地取，其余落 PyPI：

```dockerfile
# syntax=docker/dockerfile:1
FROM alpine:3.24
RUN apk add --no-cache python3 uv
RUN --mount=type=bind,from=a76yyyy/onnxruntime:1.24.4-wheelhouse,source=/wheels,target=/w \
    uv pip install --system --break-system-packages --no-cache \
        --index-url https://pypi.org/simple \
        --extra-index-url file:///w/simple \
        -r requirements.txt
```

- 本地索引放在 `--extra-index-url`：uv 的索引按定义顺序查询，默认索引（PyPI）永远最低优先级
- 索引页里的链接是相对路径，挂到 `/w` 还是 `/wheels` 都成立
- 本地有该包但版本不满足时解析直接失败，不会静默改从 PyPI 装另一个版本

全本地、零外网时改用 `--no-index --find-links /w`（`/w` 下仍是平铺的 wheel）。

需要 BuildKit（首行 `# syntax=docker/dockerfile:1`，Docker 23 起默认启用）。构建器不支持
`RUN --mount`（如 Kaniko）或运行时还要用 `/wheels` 时，改用 COPY：

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4-wheelhouse AS onnx
FROM alpine:3.24
COPY --from=onnx /wheels /wheels
RUN apk add --no-cache python3 uv && \
    uv pip install --system --break-system-packages --no-cache \
        --no-index --find-links /wheels numpy==2.4.6 && \
    uv pip install --system --break-system-packages --no-cache \
        --find-links /wheels onnxruntime==1.24.4
```

两种写法装出的包完全一致，只差体积（arm64）：

| 写法 | 镜像 | `/wheels` 留在镜像里 |
|---|---|---|
| `RUN --mount=type=bind,from=<wheelhouse>` | 279 MB | 否 |
| `COPY --from=<wheelhouse> /wheels` | 334 MB | 是（`rm` 无效：COPY 与 RUN 是两层） |

多个 wheelhouse 叠加（`--mount` 与 `--extra-index-url` 都可重复，顺序即优先级）：

```dockerfile
RUN --mount=type=bind,from=a76yyyy/onnxruntime:1.24.4-wheelhouse,source=/wheels,target=/w/a \
    --mount=type=bind,from=<其他 wheelhouse 镜像>,source=/wheels,target=/w/b \
    uv pip install --system --break-system-packages --no-cache \
        --index-url https://pypi.org/simple \
        --extra-index-url file:///w/a/simple \
        --extra-index-url file:///w/b/simple \
        -r requirements.txt
```

直接基于 runtime 镜像：

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4
```

## 镜像内容

| 组件 | wheelhouse | runtime |
|---|---|---|
| 基础镜像 | `scratch` | `alpine:3.24`；loong64 为 `ghcr.io/loong64/alpine:3.24` |
| Python | — | 3.14 |
| onnxruntime | 1.24.4（`musllinux_1_2` wheel） | 同左 |
| numpy | 2.4.6（`musllinux_1_2` wheel） | 同左 |
| 随 wheel 自带的依赖 | `onnxruntime.libs/`、`numpy.libs/`：protobuf、abseil、re2、ICU、openblas、libgfortran、libstdc++、libgcc_s | 已展开到 site-packages |
| 内容位置 | `/wheels` | `/usr/lib/python3.14/site-packages` |
| 包管理器 | — | `uv`（python3 不自带 pip；装包时用得上） |

wheel 自包含，装进干净的 alpine 即可 import 并推理，不需要 apk 提供上述库。

## 支持的平台

| 平台 | wheelhouse | runtime | 轮子架构名 |
|---|---|---|---|
| `linux/amd64` | ✓ | ✓ | `x86_64` |
| `linux/arm64` | ✓ | ✓ | `aarch64` |
| `linux/arm/v7` | ✓ | ✓ | `armv7l` |
| `linux/ppc64le` | ✓ | ✓ | `ppc64le` |
| `linux/riscv64` | ✓ | ✓ | `riscv64` |
| `linux/loong64` | ✓ | ✓ | `loongarch64` |
| `linux/386` | ✗ | ✗ | — |
| `linux/arm/v6` | ✗ | ✗ | — |
| `linux/s390x` | ✗ | ✗ | — |

三个不支持的平台：

| 平台 | 原因 |
|---|---|
| `linux/386` | Alpine 配方 `arch="all !s390x !x86"` 排除，其中 `x86` 指 32 位 i386 |
| `linux/s390x` | 同上 |
| `linux/arm/v6` | Alpine 的 armhf 分支被 `py3-flatbuffers` 挡住，没有 `py3-onnxruntime` |

`loong64` 没有官方 alpine 镜像，构建时传 `--build-arg BASE_IMAGE=ghcr.io/loong64/alpine:3.24`；
该仓库有同版本的 `py3-onnxruntime` 与 `py3-numpy`。

## 镜像体积

| 镜像 | 首次拉取 | 占用磁盘 | 容器内 |
|---|---|---|---|
| `1.24.4-wheelhouse`（arm64） | 26.0 MiB | 54.8 MB | 26 MiB（两个 wheel） |
| `1.24.4`（arm64） | 63.4 MiB | 282 MB | 178 MB（其中 site-packages 128 MB） |
| `1.24.4`（armv7） | — | 237 MB | 103 MB（site-packages） |

`占用磁盘` 是 `docker images` 的口径：containerd 计入解压后的层与压缩 blob 两份，约为
`首次拉取` 的两倍。

## 产物

```
onnxruntime-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
numpy-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
SHA256SUMS
simple/
  index.html                     # 项目列表
  <project>/index.html           # 指向 ../../<wheel>#sha256=...
```

`simple/` 是 PEP 503 索引，由 `pack_wheel.py --emit-index` 在打包末尾生成。链接用相对路径，
整份 wheelhouse 可挂到任意目录；平铺的 wheel 保留在原处，`--find-links` 的用法不受影响。

标签由 `auditwheel repair` 生成。

| 标签 | 含义 | PyPI 接受 |
|---|---|---|
| `musllinux_1_2_<arch>` | 在 musl ≥ 1.2 上构建，除 musl 外不依赖系统库 | 是 |
| `linux_<arch>` | 只说明 CPU 架构，不保证 libc 版本或依赖齐全 | 否 |

## 构建

```bash
# wheelhouse 内容（/wheels）
docker buildx build --platform linux/arm64 --target wheel \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --output type=local,dest=./dist .

# 可运行镜像
docker buildx build --platform linux/arm64 --target runtime \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    -t onnxruntime:1.24.4 .
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `ALPINE_VERSION` | `3.24` | 决定 wheel 的 python tag（当前 `cp314`） |
| `BASE_IMAGE` | `alpine:${ALPINE_VERSION}` | loong64 传 `ghcr.io/loong64/alpine:3.24` |
| `ONNX_VERSION` | `1.24.4` | 与 apk 里的版本不一致时构建失败 |
| `NUMPY_VERSION` | `2.4.6` | 同上，只做断言 |
| `APK_MIRROR` | 空 | 如 `https://mirrors.tuna.tsinghua.edu.cn` |
| `PIP_MIRROR` | 空 | 装 auditwheel 用；空 = 官方 PyPI |
| `AUDITWHEEL_VERSION` | `6.8.2` | 负责依赖收集与打标 |

## 依赖处理

### numpy 的来源与差异

- onnxruntime 声明 `Requires-Dist: numpy>=1.21.6`
- PyPI 的 numpy musllinux 轮子只覆盖 `x86_64` / `aarch64`；`armv7l` / `riscv64` / `ppc64le` /
  `loongarch64` 缺失，源装需要编译器，runtime 镜像没有
- 六个架构都自建（`--from-apk --package numpy`），保证同一份产物、消费者只写一条安装命令

本地索引负责「优先用自建的 numpy」：它排在 PyPI 之前，同样的版本号一定从本地取。
若改用 `--find-links`（无优先级保证），`x86_64` / `aarch64` 上 uv 可能静默改走 PyPI，
自建的那份反而没被验证——这是早期用 `--no-index` 强制的原因。

自建版与 PyPI 官方轮子的差异：

| | 自建（Alpine 的 `py3-numpy`） | PyPI 官方 |
|---|---|---|
| OpenBLAS | Alpine 系统包 | `scipy-openblas64` |
| `DYNAMIC_ARCH` | 0（编译期锁定 armv8 基线） | 1（运行期按 CPU 派发） |
| BLAS 整数宽度 | 32 位 | 64 位 |
| 覆盖架构 | 六个 | `x86_64` / `aarch64` |

OCR 与数据处理不走 BLAS，因此未按架构拆分；需要 BLAS 峰值性能的消费者改用 PyPI 的 numpy。

### 依赖收集交给 auditwheel

打包前 wheel 里只有三个 `.so`（pybind 扩展、`libonnxruntime.so.1`、
`libonnxruntime_providers_shared.so`），其余由 `auditwheel repair` 扫 `DT_NEEDED` 补进
`<pkg>.libs/` 并改写 RPATH。以下两项它看不到：

| 项 | 处理 |
|---|---|
| `libonnxruntime_providers_shared.so` | 不是 `DT_NEEDED`，是 `Env::GetRuntimePath()` 拼路径 `dlopen` 的，必须自己放进 `capi/` |
| `capi/libonnxruntime.so.1` | 不在 pybind 的 `DT_NEEDED` 里（apk 版的 pybind 自带静态 ORT），留给 C/C++ API 与 `dlopen` |

运行前提：

- 必须在装了这些依赖的镜像里跑，干净镜像会报
  `Cannot repair wheel, because required library "libabsl_..." could not be located`
- 不传 `--plat`：从 ELF interpreter 认出 `libc=musl`，自动给出 `musllinux_1_2_<arch>`

### 两个省体积的做法

| 做法 | 原因 |
|---|---|
| `RUN --mount=type=bind,from=repack` 挂 `/wheels`，不用 `COPY` + `rm` | COPY 与 RUN 是两层，删掉的字节仍在下层，镜像大小照样算 |
| `uv pip install` 带 `--no-cache` | uv 默认把解压后的包再存一份到 `/root/.cache/uv`，比两个包本身还大 |

`apk add python3` 仍然需要（装解释器），但不需要 `py3-numpy`。

## 已知限制

- wheel 里没有 ICU 数据文件（`/usr/share/icu/*/icudt*l.dat`），只影响 `libre2` 的本地化正则。
  需要时上层 `apk add icu-data-en`。
- sympy + mpmath 是 runtime 镜像里最大的一块（纯 Python），是 onnxruntime 的硬依赖，去掉会
  破坏形状推断。
- numpy 的 dist-info 有一条 `License-File` 指向 Alpine 包本身就没打的目录，安装时 pip 告警
  但不失败。
- 构建被绑在 Alpine 的编译选项上（无 `-march` 调优、EP 组合固定）。

## 维护者

<details>
<summary>CI 结构</summary>

`.github/workflows/_image.yml` 是共享 workflow，`Build Image.yml` 是薄调用方。四段：
plan → build → smoke → publish。

- 只在 `workflow_dispatch` 与 tag push 时构建，普通 push 不触发
- 每平台一个 job，一次构建两个 target（共享 repack 阶段）
- 发布模式两个出口各自 `push-by-digest`，artifact 按出口分名（`digest-wheelhouse-*` /
  `digest-runtime-*`），publish 分别合并，不跨出口
- 冒烟直接跑 runtime 镜像：环境架构、numpy 与一次线性代数、onnxruntime 版本与 provider、
  四个产物的 ELF `e_machine`、一次真实推理
- 闸门 fail-closed：有发布意图 && build 与 smoke 都 `success` && 平台集合完整，才合并
  manifest；合并后回读 manifest 按集合比对
- 推送开关写进 `outputs` 字符串：`push-by-digest=true` 不带 `push=true` 时 buildkit 只写
  本地镜像库、静默不推送

</details>

<details>
<summary>源码编译路径（已归档）</summary>

`archive/source-build` 分支保留从源码编译 onnxruntime 的版本（`Dockerfile` + `alpine/`
配方快照），用途是 Alpine 的包缺失或需要自己调 cmake 开关。它只产 onnxruntime 的 wheel、
不产 numpy 与 runtime 镜像，也不在 CI 覆盖范围内。

</details>

<details>
<summary>pack_wheel.py</summary>

两个入口：`--wheel-dir`（已归档的源码路径用）与 `--from-apk`（当前路径用，`--package` 选包）。
保留的两步：解引用 apk 里的符号链接、按 SONAME 改名（`libonnxruntime.so.1.24.4` →
`libonnxruntime.so.1`）。后者不能留软链——`package_data` 会跟随软链，同一份内容打两遍。

</details>
