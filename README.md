# OnnxRuntime Docker

把 onnxruntime 编成**自包含的 Python wheel**，供上层镜像（ddddocr / qd 等）用 uv 安装。
本仓库不再产出 apk 包。免编译路径还会把 **numpy** 一起重打包，让 wheelhouse 在这几个
架构上自给自足。

- 源码：[qd-today/onnxruntime-docker](https://github.com/qd-today/onnxruntime-docker)
- 版本：onnxruntime 1.24.4 · alpine 3.24（Python 3.14，wheel tag `cp314`）

## 快速开始

CI 为每个架构发布**两个镜像**：

| tag | 内容 | 用途 |
|---|---|---|
| `1.24.4-wheelhouse` | 只有 `/wheels`（onnxruntime + numpy 两个 wheel） | 给上层镜像 `COPY --from` 取 wheel |
| `1.24.4` | 可运行镜像，已装好 onnxruntime 与 numpy | 直接用 |
| `wheelhouse` / `latest` | 上面两者的滚动标签 | 跟最新 |

```dockerfile
# 取 wheel 自己组装
FROM a76yyyy/onnxruntime:1.24.4-wheelhouse AS onnx
FROM alpine:3.24
COPY --from=onnx /wheels /wheels
RUN apk add --no-cache python3 uv && \
    uv pip install --system --break-system-packages \
        --no-index --find-links /wheels numpy==2.4.6 && \
    uv pip install --system --break-system-packages \
        --find-links /wheels onnxruntime==1.24.4
```

```dockerfile
# 或者直接基于 runtime 镜像
FROM a76yyyy/onnxruntime:1.24.4
```

numpy 单独一行装、且带 `--no-index`，是为了保证它来自 wheelhouse 而不是 PyPI：
`armv7` / `riscv64` / `ppc64le` 上 PyPI 根本没有 numpy 的 musllinux 轮子，退回源码
编译会因为镜像里没有编译器而失败。`aarch64` / `amd64` 上 PyPI 有轮子，不加
`--no-index` 就会静默改走 PyPI，等于这三个架构的路径没被验证过。

本地构建并把 wheel 取到 `./dist/wheels/`：

```bash
docker buildx build --platform linux/arm64 \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --target wheel --output type=local,dest=./dist -f Dockerfile.repackage .
```

`--target runtime` 出可运行镜像；`Dockerfile`（源码编译）只能 `--target wheel`，
没有 runtime 出口，CI 因此固定用 `Dockerfile.repackage`。

两个 Dockerfile 都要在构建镜像里 `pip install auditwheel`（版本由
`AUDITWHEEL_VERSION` 指定，默认 6.8.2）。`APK_MIRROR` 之外还有个 `PIP_MIRROR`
（默认空 = 官方 PyPI），国内构建时一并给上：

```bash
docker buildx build --platform linux/arm64 \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --build-arg PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple \
    --output type=local,dest=./dist .
```

## 免编译路径（复用 Alpine 官方 apk 重打包）

`Dockerfile.repackage` 不编译 onnxruntime，直接把 Alpine 官方的 `py3-onnxruntime`
重打包成**与源码路径同名同结构**的 wheel（同样是 `FROM scratch` 的 `/wheels` +
`SHA256SUMS`），CI 与 `tests/smoke.py` 一行都不用改：

```bash
docker buildx build --platform linux/ppc64le \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --target wheel --output type=local,dest=./dist -f Dockerfile.repackage .
```

自包含化逻辑与源码路径共用 `pack_wheel.py`（`--from-apk` 分支）：把 `/usr/lib` 里的
`libonnxruntime.so.1.24.4` 解引用复制成 `capi/libonnxruntime.so.1`（按 SONAME 命名，
不放软链），`libonnxruntime_providers_shared.so` 也一并放进 `capi/`（它是 `dlopen`
的，auditwheel 看不到），剩下的 protobuf / abseil / re2 / ICU 依赖交给
`auditwheel repair`：它把库改名成 `<name>-<hash>.so` 塞进 `onnxruntime.libs/`，把
RPATH 设成 `$ORIGIN:$ORIGIN/../../onnxruntime.libs`，并把标签重打成
`musllinux_1_2_<arch>`。

同一条分支（`--package`）还顺手把 **numpy** 也重打包了，理由与做法：

- onnxruntime 依赖 `numpy>=1.21.6`，而 numpy 在 **armv7 / riscv64 / ppc64le 上 PyPI
  没有 musllinux 轮子**（只有 amd64 / aarch64 有），`uv pip install` 会退回源码编译，
  运行时镜像里没有编译器。
- 所以把 Alpine 的 `py3-numpy`（2.4.6）也打成 `numpy-2.4.6-cp314-cp314-musllinux_1_2_<arch>.whl`
  放进同一个 `/wheels`，`--find-links` 就能取到。
- numpy 的 `.so` 是包内真文件，不走 onnxruntime 的「解软链 + 按 SONAME 改名」那套；
  auditwheel 会把 `/usr/lib/libopenblas.so.3`（10.8 MiB）以及它依赖的 `libgfortran` /
  `libstdc++` / `libgcc_s` 收进 `numpy.libs/` 并改好 RPATH，所以 BLAS 在干净镜像里
  可用（`tests/smoke.py` 会真的跑一次 `numpy.linalg.solve` / `svd` 来验）。
- 这里必须用 apk 装 numpy 而**不能** pip 装：pip 拿到的 wheel 已经 bundle 过一遍
  openblas，再 repair 会嵌套一层 `.libs`。

auditwheel 靠遍历 musl 搜索路径定位每个 `DT_NEEDED`，所以**必须在装好这些依赖的
构建镜像里跑**；换到只有 python3 的干净镜像里会直接报
`Cannot repair wheel, because required library "libabsl_..." could not be located`。

能省多少（aarch64，同一台 arm64 机器）：

| | 源码编译（`Dockerfile`） | apk 重打包（`Dockerfile.repackage`） |
|---|---|---|
| 构建耗时 | 18m42s | 37s（含拉基础镜像；重打包本身 1.7s） |
| wheel 大小 | 16.3 MiB | 14.5 MiB |
| `capi/` 里 `.so` 数 | 3 | 3 |
| `onnxruntime.libs/` 里 `.so` 数 | 53 | 50 |

QEMU 下的差距更极端：`ppc64le` 重打包 45s、`riscv64` 79s、`armv7` 85s（三者都跑过
`tests/smoke.py` 且 PASS），而源码编译要数小时、有撞 GitHub Actions 6 小时上限的风险。

代价是**被绑在 Alpine 官方的编译选项上**（没有编译期的 `-march` 调优、EP 组合固定），
拿不到自己改 cmake 开关的自由。

## runtime 出口

`Dockerfile.repackage` 的最后一个阶段 `runtime` 是一个**可直接运行**的镜像：
`alpine:3.24` + `apk add python3 uv`，numpy 和 onnxruntime 全部从 wheelhouse 装，
**不再 `apk add py3-numpy`**。

```bash
docker buildx build --platform linux/arm/v7 --target runtime \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    -t onnxruntime-runtime:1.24.4 -f Dockerfile.repackage .
```

（tag 名由 CI 决定，这里只是个例子。）

要点：

- **wheelhouse 不进镜像**。用 BuildKit 的 `RUN --mount=type=bind,from=repack` 把
  `/wheels` 只挂进装包那一条 `RUN`，装完随 layer 一起消失。不能写成
  `COPY --from=repack /wheels /wheels` + `RUN rm -rf /wheels`——COPY 与 RUN 是两层，
  删掉的字节还在下层，`docker images` 照样算进去（本架构两个 wheel 合计约 25MB 白花）。
- 需要给上层 `COPY --from` 复用 wheel 的，用 `--target wheel` 出的
  `a76yyyy/onnxruntime:<ver>`（同架构），它本来就只有 `/wheels`。
- 两条 `uv pip install` 都带 `--no-cache`：uv 默认会把解压后的包再存一份到
  `/root/.cache/uv`，原样进镜像层（实测多出 130MB）。
- `apk add python3` 仍然必须：装的是**解释器**。两个 wheel 各自把
  `libstdc++` / `libgcc_s` / `openblas` 收在自己的 `.libs` 下，不依赖 apk 提供。

体积（实测，`--target runtime`）：

| 架构 | runtime 镜像 | 其中 site-packages | numpy wheel | onnxruntime wheel |
|---|---|---|---|---|
| `linux/arm64` | 282 MB | 127 MB | 11.7 MiB | 14.5 MiB |
| `linux/arm/v7` | 237 MB | 103 MB | 10.5 MiB | 14.6 MiB |

site-packages 里最大的是 sympy（28.5 MB，onnxruntime 的硬依赖）、onnxruntime（35 MB，
含 30.6 MB 的 `capi/`）、numpy（23.5 MB，含 11 MB openblas）、`onnxruntime.libs`（18.8 MB）、
`numpy.libs`（16.9 MB）。没有可以白拿的部分。

## 产物

`Dockerfile.repackage` 的 `wheel` 出口一次给两个 wheel + 一份校验和：

```
onnxruntime-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
numpy-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
SHA256SUMS        # 覆盖以上两个
```

标签由 `auditwheel repair` 从 `linux_<arch>` 改成 `musllinux_1_2_<arch>`，更准确地
表达了「只能在 musl 上用」。`Dockerfile`（源码编译路径）只产 onnxruntime 那个。

wheel 是自包含的：`capi/` 里只有 pybind 扩展、`libonnxruntime.so.1` 和
`libonnxruntime_providers_shared.so`；protobuf / abseil / re2 / ICU / libstdc++ /
libgcc_s 都在 `onnxruntime.libs/` 下（auditwheel 的 `<name>-<hash>.so` 命名），
RPATH 为 `$ORIGIN:$ORIGIN/../../onnxruntime.libs`。装进干净的 alpine 就能 import
并推理，不需要 apk 提供这些库。

## 平台

默认构建 `linux/amd64,linux/arm64,linux/arm/v7`。`ppc64le`、`riscv64`、`arm/v6`
只在显式指定时构建——它们在 QEMU 下要编译数小时。

`386` 与 `s390x` 不支持：onnxruntime 上游在这两个架构编译失败，与 Alpine 官方配方的
`arch="all !s390x !x86"` 一致。`loong64` 需要先换 loong64 基座镜像。

走 `Dockerfile.repackage` 时可用架构 = Alpine 官方支持的那几个：`aarch64` / `armv7` /
`ppc64le` / `riscv64`，且**不受 QEMU 编译耗时限制**（每个都在 1～2 分钟内）。`386` /
`s390x` / `loong64` 会在构建时直接报错退出，报错里写明原因。这四家的 Alpine 都有
`py3-numpy`，所以 numpy wheel 不会缺席；反过来，`amd64` / `aarch64` 之外 PyPI 没有
numpy 的 musllinux 轮子，另一个原因见上文「免编译路径」。

## 构建参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `ALPINE_VERSION` | `3.24` | 决定 wheel 的 python tag（当前为 `cp314`） |
| `ONNX_VERSION` | `1.24.4` | onnxruntime 版本 |
| `NUMPY_VERSION` | `2.4.6` | numpy 版本（仅 `Dockerfile.repackage`；runtime 阶段也用它钉住安装版本） |
| `APK_MIRROR` | 空 | 如 `https://mirrors.tuna.tsinghua.edu.cn` |
| `GIT_DOMAIN` | `https://github.com` | 可用 `https://gh-proxy.com/https://github.com` 加速 |

`Dockerfile.repackage` 只认 `ALPINE_VERSION` / `ONNX_VERSION` / `NUMPY_VERSION` /
`APK_MIRROR`；它不拉源码，`GIT_DOMAIN` 用不上。`ONNX_VERSION` / `NUMPY_VERSION` 只做
断言——apk 里的版本与它不一致时直接构建失败，避免 Alpine 升级后悄悄产出一个版本号对
不上的 wheel。

## 配方出处

编译开关与 patch 顺序照抄 Alpine 官方 `community/onnxruntime`，配方快照在 `alpine/`。
与官方配方有两处差异：

- `system.patch` 跳过。它针对 1.22 及更早——1.24.4 上游已改成「先 `find_package`，
  找不到才下载」，装了 `abseil-cpp-dev` / `protobuf-dev` / `re2-dev` / `nlohmann-json`
  之后效果一致。
- `upb-fix.patch` 跳过。它只在「源码编译 protobuf」那条路上有意义；用系统 protobuf 时
  会与 `protobuf-config.cmake` 重复定义 target，配置直接失败。

## 已知限制

wheel 里没有 ICU 的数据文件（`/usr/share/icu/*/icudt*l.dat`），只影响 `libre2` 的
本地化正则。上层需要时 `apk add icu-data-en`。

`libstdc++.so.6` / `libgcc_s.so.1` 会被 `auditwheel repair` 一起收进
`onnxruntime.libs/`（musllinux policy 不把它们当系统库；numpy 那边同理，走
`libopenblas` → `libgfortran` → `libstdc++` 的链）。所以 wheel 不依赖 apk 提供这两个库，
换**非 apk 的 python**（静态编译、conda、pyenv 自编译）也能用。

重打包路径与源码路径的另一个差异：apk 里的 `pybind11_state.so` 没有把
`libonnxruntime.so.1` 写进 DT_NEEDED（它自己静态含了 ORT），`capi/libonnxruntime.so.1`
实际是给 C/C++ API 或 dlopen 用的。两条路径都按 SONAME 保留它，产物结构保持一致。

numpy 的 `Requires-Dist`（flatbuffers / numpy / packaging / protobuf / sympy）里，
sympy + mpmath 独占约 30MB 且是纯 Python —— 这是 runtime 镜像里最大的一块，去掉会
破坏 onnxruntime 的形状推断，不建议。
