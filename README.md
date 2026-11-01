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
# syntax=docker/dockerfile:1.7
FROM alpine:3.24
RUN --mount=type=bind,from=a76yyyy/onnxruntime:wheelhouse,source=/wheels,target=/w1 \
    --mount=type=bind,from=a76yyyy/onnxruntime:wheelhouse,source=/wheels,target=/w2 \
    apk add --no-cache $(cat /w1/system-packages.txt) uv && \
    uv pip install --system --break-system-packages --no-cache \
        --index-url https://pypi.org/simple \
        --extra-index-url file:///w1/simple \
        --extra-index-url file:///w2/simple \
        "numpy==2.4.6" "onnxruntime==1.24.4"
```

| 写法 | 原因 |
|---|---|
| `apk add $(cat /w1/system-packages.txt)` | 清单是 wheelhouse 的运行时前置条件（解释器与 `libz`），写错会在这里构建失败 |
| `--index-url https://pypi.org/simple` | 默认索引是**最低**优先级，纯 Python 依赖（flatbuffers / packaging / protobuf / sympy）从这里兜底 |
| `--extra-index-url file:///wN/simple` | 每个 wheelhouse 一个，按定义顺序查询；first-index 策略下第一个含该包的索引胜出。实测（往本地 wheel 里塞标记文件后安装）：本地那份一定被选中 |
| 发行名与 PyPI 同名 | wheelhouse 里的 wheel 用 PyPI 同名发行名（如 `opencv-python-headless`），用户换回 PyPI 版不用改任何配置；功能差异只在本仓库刻意裁剪的那部分 |

`/w1/simple` 是 PEP 503 索引，链接为相对路径，可与 wheel 目录一起挂到任意位置。| 标签 | 含义 | PyPI 接受 |
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
- wheel 的 zip 条目带 mtime，构建不可逐字节复现；内容一致，`sha256` 每次不同。

## 维护者

<details>
<summary>版本参数</summary>

上游发新版时，改这里或 dispatch 时传同名输入即可构建新版本；产物版本与传入版本不一致时
构建失败（`--expect-version` 断言）。

| 参数 | 作用 | 默认值 |
|---|---|---|
| `onnx_version` | onnxruntime 版本，同时是 runtime 镜像的 tag | 1.24.4 |
| `numpy_version` | numpy 版本（决定 wheelhouse 里那个 numpy wheel） | 2.4.6 |
| `alpine_version` | alpine 版本，决定 Python 小版本与 wheel 的 cpXX tag | 3.24 |

这两个包来自 Alpine 的 apk 预编译包（`py3-onnxruntime` / `py3-numpy`），版本受 apk 仓限制：
参数只能钉住 apk 里已有的版本，apk 更新后再改这里（版本对不上时构建会在断言处失败，
不会产出错标版本的 wheel）。PyPI 的 musllinux 轮子只覆盖 x86_64 与 aarch64，其余架构
只能走 apk 或自行源码构建。

</details>

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
<summary>src/pack_wheel.py</summary>

三个入口：`--wheel-dir`（已归档的源码路径）、`--from-apk`（当前路径，`--package` 选包）、`--emit-index`（生成 PEP 503 索引）。保留的两步：解引用 apk 里的符号链接、按 SONAME 改名（`libonnxruntime.so.1.24.4` → `libonnxruntime.so.1`）；软链不能进 wheel，`package_data` 会跟随软链导致同一份内容打两遍。

</details>
