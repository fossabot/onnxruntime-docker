# OnnxRuntime Docker

把 onnxruntime 编成**自包含的 Python wheel**，供上层镜像（ddddocr / qd 等）用 uv 安装。
本仓库不再产出 apk 包。

- 源码：[qd-today/onnxruntime-docker](https://github.com/qd-today/onnxruntime-docker)
- 版本：onnxruntime 1.24.4 · alpine 3.24（Python 3.14，wheel tag `cp314`）

## 快速开始

CI 发布 `a76yyyy/onnxruntime:1.24.4` 与 `:latest` 的多架构镜像。镜像里只有 `/wheels`，
不是可运行镜像，用途是给上层镜像取 wheel：

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4 AS onnx
FROM alpine:3.24
COPY --from=onnx /wheels /wheels
RUN apk add --no-cache python3 uv && \
    uv pip install --system --break-system-packages \
        --no-index --find-links /wheels onnxruntime==1.24.4
```

本地构建并把 wheel 取到 `./dist/wheels/`：

```bash
docker buildx build --platform linux/arm64 \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --output type=local,dest=./dist .
```

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

## 产物

`onnxruntime-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl`，另有 `SHA256SUMS`
（标签由 `auditwheel repair` 从 `linux_<arch>` 改成 `musllinux_1_2_<arch>`，
更准确地表达了「只能在 musl 上用」）。

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
`s390x` / `loong64` 会在构建时直接报错退出，报错里写明原因。

## 构建参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `ALPINE_VERSION` | `3.24` | 决定 wheel 的 python tag（当前为 `cp314`） |
| `ONNX_VERSION` | `1.24.4` | onnxruntime 版本 |
| `APK_MIRROR` | 空 | 如 `https://mirrors.tuna.tsinghua.edu.cn` |
| `GIT_DOMAIN` | `https://github.com` | 可用 `https://gh-proxy.com/https://github.com` 加速 |

`Dockerfile.repackage` 只认 `ALPINE_VERSION` / `ONNX_VERSION` / `APK_MIRROR`；
它不拉源码，`GIT_DOMAIN` 用不上。`ONNX_VERSION` 只做断言——apk 里的版本与它不一致时
直接构建失败，避免 Alpine 升级后悄悄产出一个版本号对不上的 wheel。

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

`libstdc++.so.6` / `libgcc_s.so.1` 没有打进 wheel，因为 apk 版 `python3` 依赖
`so:libmpdec.so.4`，而 `mpdecimal` 依赖 `libstdc++`——只要 python3 是 apk 装的，这两个库
必然在 `/usr/lib`。已验证：干净 `alpine:3.24` + `apk add python3 uv` 下安装本 wheel，
运行时 `libonnxruntime.so.1` / `libicuuc.so.78` / `libre2.so.11` 全部来自
`site-packages/onnxruntime/capi/`，`libstdc++.so.6` / `libgcc_s.so.1` 来自 `/usr/lib`。
如果上层用的是**非 apk 的 python**（静态编译、conda、pyenv 自编译），需要自行提供这两个库。

重打包路径与源码路径的另一个差异：apk 里的 `pybind11_state.so` 没有把
`libonnxruntime.so.1` 写进 DT_NEEDED（它自己静态含了 ORT），`capi/libonnxruntime.so.1`
实际是给 C/C++ API 或 dlopen 用的。两条路径都按 SONAME 保留它，产物结构保持一致。
