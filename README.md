# OnnxRuntime Docker

把 onnxruntime 打成**自包含的 Python wheel**（连同它依赖的 numpy），供 ddddocr / qd 等
上层镜像用 uv 安装。不产出 apk 包。

- 源码：[qd-today/onnxruntime-docker](https://github.com/qd-today/onnxruntime-docker)
- 版本：onnxruntime 1.24.4 · numpy 2.4.6 · alpine 3.24（Python 3.14，wheel tag `cp314`）

## 快速开始

CI 为每个架构发布两个镜像：

| tag | 内容 | 用途 |
|---|---|---|
| `1.24.4-wheelhouse` | 只有 `/wheels`（onnxruntime + numpy 两个 wheel + `SHA256SUMS`） | 给上层镜像取 wheel |
| `1.24.4` | 可运行镜像，已装好 onnxruntime 与 numpy | 直接用 |
| `wheelhouse` / `latest` | 上面两者的滚动标签 | 跟最新 |

取 wheel 自己组装：

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4-wheelhouse AS onnx
FROM alpine:3.24
COPY --from=onnx /wheels /wheels
RUN apk add --no-cache python3 uv && \
    uv pip install --system --break-system-packages \
        --no-index --find-links /wheels numpy==2.4.6 && \
    uv pip install --system --break-system-packages --no-cache \
        --find-links /wheels onnxruntime==1.24.4
```

或者直接基于 runtime 镜像：

```dockerfile
FROM a76yyyy/onnxruntime:1.24.4
```

numpy 单独一行、且带 `--no-index`，是为了保证它来自 wheelhouse 而不是 PyPI。原因见
下文「为什么 numpy 也在里面」。

也可以在一次构建里挂多个 wheelhouse（每个 `--mount` 一个来源，`--find-links` 可重复）：

```dockerfile
RUN --mount=type=bind,from=a76yyyy/onnxruntime:1.24.4-wheelhouse,source=/wheels,target=/w/onnx \
    --mount=type=bind,from=其他仓库的 wheelhouse,source=/wheels,target=/w/other \
    uv pip install --system --break-system-packages --no-cache \
        --find-links /w/onnx --find-links /w/other onnxruntime==1.24.4 numpy==2.4.6
```

## 产物

```
onnxruntime-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
numpy-<ver>-cpXXX-cpXXX-musllinux_1_2_<arch>.whl
SHA256SUMS        # 覆盖以上两个
```

`<arch>` 是 PyPI 的架构名：`x86_64` / `aarch64` / `armv7l` / `ppc64le` / `riscv64` /
`loongarch64`。

标签由 `auditwheel repair` 从 `linux_<arch>` 改成 `musllinux_1_2_<arch>`。两者差在承诺：
`linux_<arch>` 只说明 CPU 架构，不保证 libc 版本或依赖齐全，装不上也不会被拦；`musllinux_1_2`
表示「在 musl ≥ 1.2 上构建、除 musl 外不依赖系统库」。

wheel 是自包含的：`capi/` 里只有 pybind 扩展、`libonnxruntime.so.1` 和
`libonnxruntime_providers_shared.so`；其余依赖（protobuf / abseil / re2 / ICU /
libstdc++ / libgcc_s，numpy 则是 openblas / libgfortran 那一串）都在 `*.libs/` 下，
RPATH 指向 `$ORIGIN` 或 `$ORIGIN/../../<pkg>.libs`。装进干净的 alpine 就能 import 并推理，
不需要 apk 提供这些库。

## 平台

```
linux/amd64 · linux/arm64 · linux/arm/v7 · linux/ppc64le · linux/riscv64 · linux/loong64
```

`loong64` 没有官方 alpine 镜像，走 `ghcr.io/loong64/alpine`（该仓库有同版本的
`py3-onnxruntime` / `py3-numpy`），因此基座做成 `BASE_IMAGE` 参数，构建时传
`--build-arg BASE_IMAGE=ghcr.io/loong64/alpine:3.24`。

`386`、`arm/v6`、`s390x` 不支持。前两个是 Alpine 配方 `arch="all !s390x !x86"` 排除的
（`x86` 指 32 位 i386，不是 amd64），armv6 的 Python 绑定被 `py3-flatbuffers` 挡住。
ddddocr 也不在这三个平台上提供 OCR。

## 构建

两条路径，产出同名的 wheel：

**重打包（主路径，CI 用）** —— `Dockerfile.repackage` 直接复用 Alpine 官方的
`py3-onnxruntime` / `py3-numpy`，不编译：

```bash
docker buildx build --platform linux/arm64 -f Dockerfile.repackage \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --target wheel --output type=local,dest=./dist .
```

两个出口：`--target wheel` 出 `/wheels`（wheelhouse 镜像的内容），`--target runtime` 出
可运行镜像。

**源码编译（兜底）** —— `Dockerfile` 从 onnxruntime 源码构建，编译开关与 patch 顺序照抄
Alpine 官方 `community/onnxruntime`（配方快照在 `alpine/`）。只有 `--target wheel`，
不产出 runtime 镜像，也不在 CI 里跑。用于 Alpine 的包缺失或需要自己调 cmake 开关的场景：

```bash
docker buildx build --platform linux/arm64 --target wheel \
    --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    --output type=local,dest=./dist .
```

### 构建参数

| 参数 | 默认 | 适用 | 说明 |
|---|---|---|---|
| `ALPINE_VERSION` | `3.24` | 两者 | 决定 wheel 的 python tag（当前 `cp314`） |
| `BASE_IMAGE` | `alpine:${ALPINE_VERSION}` | 重打包 | loong64 传 `ghcr.io/loong64/alpine:3.24` |
| `ONNX_VERSION` | `1.24.4` | 两者 | 与 apk 里的版本不一致时构建失败 |
| `NUMPY_VERSION` | `2.4.6` | 重打包 | 同上，只做断言 |
| `APK_MIRROR` | 空 | 两者 | 如 `https://mirrors.tuna.tsinghua.edu.cn` |
| `PIP_MIRROR` | 空 | 两者 | 装 auditwheel 用；空 = 官方 PyPI |
| `GIT_DOMAIN` | `https://github.com` | 源码 | 可用 `https://gh-proxy.com/...` 加速 |
| `AUDITWHEEL_VERSION` | `6.8.2` | 两者 | 收尾打标与依赖收集 |

## 依赖是怎么处理的

### 为什么 numpy 也在里面

onnxruntime 声明 `Requires-Dist: numpy>=1.21.6`，而 PyPI 上 `armv7l` / `riscv64` /
`ppc64le` **没有** numpy 的 musllinux 轮子——装的时候会退回源码编译，而 runtime 镜像里
没有编译器。所以 numpy 也用同一套手法重打包成 wheel，和 onnxruntime 放在一起。

在 `x86_64` / `aarch64` 上 PyPI 有 numpy 轮子，所以那两行必须带 `--no-index`，否则 uv 会
静默改走 PyPI，等于另外三个架构的路径没被验证过。

### auditwheel 做什么、不能做什么

依赖收集与 RPATH 全交给 `auditwheel repair`：

- 打包前 wheel 里只放三个 `.so`（pybind 扩展、`libonnxruntime.so.1`、
  `libonnxruntime_providers_shared.so`），其余由 auditwheel 扫描 `DT_NEEDED` 逐个补进
  `<pkg>.libs/`，库名加 hash 后缀，RPATH 一并改写。
- `libonnxruntime_providers_shared.so` 不是 `DT_NEEDED`，是 `Env::GetRuntimePath()`
  拼路径 `dlopen` 的，auditwheel 看不到，必须自己放进 `capi/`（和主库同目录）。
- `capi/libonnxruntime.so.1` 同样不在 pybind 的 `DT_NEEDED` 里（apk 版的 pybind 自带
  静态 ORT），它是留给 C/C++ API 与 `dlopen` 的。保留它，两条路径的产物结构才一致。
- auditwheel **必须**在装了这些依赖的镜像里跑。换到只有 python3 的干净镜像会报
  `Cannot repair wheel, because required library "libabsl_..." could not be located`。
- 不传 `--plat`：auditwheel 从 ELF interpreter 认出 `libc=musl`，自己给出
  `musllinux_1_2_<arch>`，省掉一张架构名映射表。

### 两个省体积的做法

- runtime 镜像用 `RUN --mount=type=bind,from=repack` 把 `/wheels` 只挂进装包那一条
  `RUN`，镜像里不留 `/wheels` 层。写成 `COPY` + `rm -rf` 没有效果——COPY 与 RUN 是两层，
  删掉的字节仍在下层，镜像大小照样算。
- `uv pip install` 必须带 `--no-cache`。uv 默认把解压后的包再存一份到 `/root/.cache/uv`
  并计入镜像层，这一项比两个包本身还大。

`apk add python3` 仍然必须（装的是解释器），但不需要 `py3-numpy`。

## 已知限制

- wheel 里没有 ICU 的数据文件（`/usr/share/icu/*/icudt*l.dat`），只影响 `libre2` 的
  本地化正则。需要时上层 `apk add icu-data-en`。
- sympy + mpmath 是 runtime 镜像里最大的一块（纯 Python）。它是 onnxruntime 的硬依赖，
  去掉会破坏形状推断。
- numpy 的 dist-info 里有一条 `License-File` 指向 apk 自己就没打包的目录，安装时 pip
  会告警但不会失败。
- 重打包路径被绑在 Alpine 的编译选项上（没有 `-march` 调优、EP 组合固定）；需要自己改
  cmake 开关时走 `Dockerfile`。

## 维护者

<details>
<summary>CI 结构</summary>

`.github/workflows/_image.yml` 是共享 workflow，`Build Image.yml` 是薄调用方。四段：
plan → build → smoke → publish。

- 只在 `workflow_dispatch` 与 tag push 时构建，普通 push 不触发
- 每个平台一个 job，一次构建两个 target（共享 repack 阶段，拆成两个 job 会把重打包做两遍）
- 发布模式两个出口各自 `push-by-digest`，artifact 按出口分名
  （`digest-wheelhouse-*` / `digest-runtime-*`），publish 分别合并，不跨出口
- 冒烟直接跑 runtime 镜像：`docker run <镜像> python3 tests/smoke.py <平台> <版本>`，
  校验环境架构、numpy 与一次线性代数、onnxruntime 版本与 provider、四个产物的 ELF
  `e_machine`、一次真实推理
- 闸门 fail-closed：有发布意图 && build 与 smoke 都 `success`（不是「没有 failure」，
  跳过也算不通过）&& 平台集合完整，才合并 manifest；合并后回读 manifest 按集合比对
- 推送开关写进 `outputs` 字符串：实测 `push-by-digest=true` 不带 `push=true` 时
  buildkit 只写本地镜像库、静默不推送且返回 0

</details>

<details>
<summary>两条路径与 Alpine 配方的差异</summary>

`alpine/` 是上游 `community/onnxruntime` 的配方快照，供 `Dockerfile` 使用。与官方配方
有两处差异：

- `system.patch` 不用。它面向 1.22 及更早版本；1.24.4 上游已改成「先 `find_package`，
  找不到才下载」，装上 `abseil-cpp-dev` / `protobuf-dev` / `re2-dev` / `nlohmann-json`
  之后效果一致。
- `upb-fix.patch` 不用。它只对「源码编译 protobuf」有意义；用系统 protobuf 时会与
  `protobuf-config.cmake` 重复定义 target，配置直接失败。

`pack_wheel.py` 是两条路径共用的打包脚本，两个入口：`--wheel-dir`（源码路径产出的
未打包目录）与 `--from-apk`（重打包路径，`--package` 选包）。共同保留的两步是解引用
apk 里的符号链接、按 SONAME 改名（`libonnxruntime.so.1.24.4` → `libonnxruntime.so.1`），
后者不能留软链——`package_data` 会跟随软链，同一份内容会被打两遍。

</details>
