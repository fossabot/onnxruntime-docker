# onnxruntime Docker 镜像（Alpine）

onnxruntime 与 numpy 的 musllinux wheel 分发，另附装好的可运行镜像。不产出 apk 包。

| 标签 | 内容 |
|---|---|
| `1.24.4-wheelhouse` | `scratch` + `/wheels`：onnxruntime 1.24.4、numpy 2.4.6、`SHA256SUMS`、PEP 503 索引 |
| `1.24.4` | `alpine:3.24` + Python 3.14 + 上述两个包 |
| `wheelhouse` / `latest` | 上面两者的滚动标签 |

- Docker Hub：<https://hub.docker.com/r/a76yyyy/onnxruntime>
- 源码：[qd-today/onnxruntime-docker](https://github.com/qd-today/onnxruntime-docker)

## 快速开始

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4
```

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

多 wheelhouse：

```dockerfile
ENV UV_EXTRA_INDEX_URL="file:///w/a/simple file:///w/b/simple"
```

| 写法 | 镜像（arm64） | `/wheels` 进镜像 |
|---|---|---|
| `RUN --mount=type=bind` | 279 MB | 否 |
| `COPY --from=<wheelhouse> /wheels` | 334 MB | 是（`rm` 无效，两层） |

`RUN --mount` 需要 BuildKit（首行 `# syntax=docker/dockerfile:1`）；Kaniko 等不支持时用 `COPY --from`。

## 镜像内容

| 组件 | wheelhouse | runtime |
|---|---|---|
| 基础镜像 | `scratch` | `alpine:3.24`；loong64 为 `ghcr.io/loong64/alpine:3.24` |
| Python | — | 3.14 |
| onnxruntime | 1.24.4（`musllinux_1_2` wheel） | 同左 |
| numpy | 2.4.6（`musllinux_1_2` wheel） | 同左 |
| 包管理器 | — | `uv`（python3 不含 pip） |
| ICU 数据 | — | `icu-data-en`（2.9 MB） |
| 内容位置 | `/wheels` | `/usr/lib/python3.14/site-packages` |

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

| 不支持的平台 | 原因 |
|---|---|
| `linux/386` | Alpine 配方 `arch="all !s390x !x86"`（`x86` 指 32 位 i386） |
| `linux/s390x` | 同上 |
| `linux/arm/v6` | armhf 被 `py3-flatbuffers` 挡住，无 `py3-onnxruntime` |

loong64 构建需 `--build-arg BASE_IMAGE=ghcr.io/loong64/alpine:3.24`（该仓库有同版本 `py3-onnxruntime` / `py3-numpy`）。

## 镜像体积

| 镜像 | 首次拉取 | 占用磁盘 | 容器内 |
|---|---|---|---|
| `1.24.4-wheelhouse`（arm64） | 26.0 MiB | 54.8 MB | 26 MiB |
| `1.24.4`（arm64） | 64.5 MiB | 287 MB | 181 MB（site-packages 128 MB） |
| `1.24.4`（armv7） | — | 237 MB | 103 MB |

`占用磁盘` 取 `docker images` 口径：containerd 计入解压层与压缩 blob 两份。

## 产物

```
onnxruntime-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
numpy-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
SHA256SUMS
simple/
  index.html
  <project>/index.html
```

`simple/` 由 `pack_wheel.py --emit-index` 生成，链接为相对路径（`../../<wheel>#sha256=`），可与 wheel 目录一起挂到任意位置。

| 标签 | 含义 | PyPI 接受 |
|---|---|---|
| `musllinux_1_2_<arch>` | 在 musl ≥ 1.2 上构建，除 musl 外不依赖系统库 | 是 |
| `linux_<arch>` | 只说明 CPU 架构 | 否 |

## 构建

```bash
# wheelhouse 内容（/wheels）
docker buildx build --platform linux/arm64 --target wheel \
    --output type=local,dest=./dist .

# 可运行镜像
docker buildx build --platform linux/arm64 --target runtime \
    -t onnxruntime:1.24.4 .
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `ALPINE_VERSION` | `3.24` | 决定 Python 版本与 wheel 的 `cpXX` tag |
| `BASE_IMAGE` | `alpine:${ALPINE_VERSION}` | loong64 传 `ghcr.io/loong64/alpine:3.24` |
| `ONNX_VERSION` | `1.24.4` | 与 apk 版本不一致时构建失败 |
| `NUMPY_VERSION` | `2.4.6` | 同上 |
| `APK_MIRROR` | 空 | 本地加速用，如 `https://mirrors.tuna.tsinghua.edu.cn` |
| `PIP_MIRROR` | 空 | 安装 auditwheel 用；空 = 官方 PyPI |
| `AUDITWHEEL_VERSION` | `6.8.2` | 依赖收集与打标 |

## 依赖处理

### numpy

`Requires-Dist: numpy>=1.21.6`，而 PyPI 只有 `x86_64` / `aarch64` 的 numpy musllinux 轮子，因此六个架构都从 Alpine 包重打包。

| | 自建（Alpine `py3-numpy`） | PyPI 官方 |
|---|---|---|
| OpenBLAS | Alpine 系统包 | `scipy-openblas64` |
| `DYNAMIC_ARCH` | 0（编译期锁定） | 1（运行期派发） |
| BLAS 整数宽度 | 32 位 | 64 位 |
| 覆盖架构 | 六个 | `x86_64` / `aarch64` |

OCR 与数据处理不走 BLAS，未按架构拆分。

### auditwheel

打包前 wheel 里只放三个 `.so`，其余由 `auditwheel repair` 按 `DT_NEEDED` 补进 `<pkg>.libs/` 并改写 RPATH。它看不到的两项：

| 项 | 处理 |
|---|---|
| `libonnxruntime_providers_shared.so` | 非 `DT_NEEDED`，由 `Env::GetRuntimePath()` 拼路径 `dlopen`，须手动放进 `capi/` |
| `capi/libonnxruntime.so.1` | 不在 pybind 的 `DT_NEEDED` 里，留给 C API 与 `dlopen` |

`auditwheel repair` 要从系统里解析每个 `DT_NEEDED`，因此构建镜像必须已装被解析的库；缺少时报
`Cannot repair wheel, because required library "libabsl_..." could not be located`。

| 打包目标 | 需已安装的 apk 包 | 由它带入 |
|---|---|---|
| onnxruntime | `py3-onnxruntime` | `abseil-cpp-*`、`re2`、`libprotobuf-lite`、`icu-libs` |
| numpy | `py3-numpy` | `libopenblas`、`libgfortran` |

不必装 `-dev` 包，运行时库即可。不传 `--plat`：auditwheel 从 ELF interpreter 认出 `libc=musl`。

### 体积

| 做法 | 原因 |
|---|---|
| `RUN --mount=type=bind` 挂 `/wheels` | `COPY` + `rm` 会留下下层字节 |
| `uv pip install --no-cache` | 否则解压副本写入 `/root/.cache/uv` |

## 已知限制

- 只含 `icu-data-en`（2.9 MB）。`libre2` 处理非英语 Unicode 字符类需 `apk add icu-data-full`（+31.6 MB）。
- sympy + mpmath 是 runtime 镜像里最大的一块，onnxruntime 的硬依赖，去掉会破坏形状推断。
- numpy 的 dist-info 有一条 `License-File` 指向 apk 未打包的目录，安装时 pip 告警但不失败。
- wheel 的 zip 条目带 mtime，构建不可逐字节复现；内容一致，`sha256` 每次不同。

## 维护者

<details>
<summary>CI 结构</summary>

`.github/workflows/_image.yml` 为共享 workflow，`Build Image.yml` 为调用方，四段：plan → build → smoke → publish。

- 仅 `workflow_dispatch` 与 tag push 触发
- 每平台一个 job，一次构建两个 target
- 发布时两个出口各自 `push-by-digest`，artifact 按出口分名（`digest-wheelhouse-*` / `digest-runtime-*`），publish 分别合并
- 冒烟直接跑 runtime 镜像：`tests/smoke.py` 校验环境架构、numpy 与一次线性代数、onnxruntime 版本与 provider、四个产物的 ELF `e_machine`、一次真实推理
- 闸门 fail-closed：有发布意图 && build 与 smoke 均 `success` && 平台集合完整，才合并 manifest

</details>

<details>
<summary>归档</summary>

`archive/source-build` 分支保留从源码编译 onnxruntime 的版本（`Dockerfile` + `alpine/` 配方快照）。它只产 onnxruntime 的 wheel，不产 numpy 与 runtime 镜像，不在 CI 覆盖范围内。

</details>

<details>
<summary>pack_wheel.py</summary>

三个入口：`--wheel-dir`（已归档的源码路径）、`--from-apk`（当前路径，`--package` 选包）、`--emit-index`（生成 PEP 503 索引）。保留的两步：解引用 apk 里的符号链接、按 SONAME 改名（`libonnxruntime.so.1.24.4` → `libonnxruntime.so.1`）；软链不能进 wheel，`package_data` 会跟随软链导致同一份内容打两遍。

</details>
