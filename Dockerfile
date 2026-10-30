# syntax=docker/dockerfile:1.7
#
# onnxruntime -> self-contained Python wheel
#
# 本文件把原来的「abuild 打 apk 包」流程改造成「直接产出 wheel」。
# 编译开关、patch 列表与顺序全部照抄 alpine/APKBUILD（1.24.4），改动只有：
#   * 关掉单元测试（onnxruntime_BUILD_UNIT_TESTS=OFF）
#   * 用 `setup.py bdist_wheel` 代替 `setup.py install`
#   * 把 libonnxruntime.so.* / libonnxruntime_providers_shared.so 塞进 capi，
#     再用 `auditwheel repair` 递归收集它们依赖的非系统库（abseil / protobuf /
#     re2 / icu）到 onnxruntime.libs/、把 RPATH 改成
#     $ORIGIN:$ORIGIN/../../onnxruntime.libs，并把标签重打成 musllinux_1_2_<arch>，
#     使其在任意 alpine 镜像里 `pip install` 后可直接 import
#   * 跳过两个对 1.24.4 已失效/有害的 patch（见下方说明）
#
# 用法：
#   docker buildx build --platform linux/arm64 \
#       --build-arg APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
#       --output type=local,dest=./dist .
#
# 产物：./dist/wheels/onnxruntime-<ver>-<py>-<py>-musllinux_1_2_<arch>.whl

ARG ALPINE_VERSION=3.24

########################################################################
# 1) 依赖 + 源码 + patch
########################################################################
FROM alpine:${ALPINE_VERSION} AS src

ARG ONNX_VERSION=1.24.4
ARG APK_MIRROR=""
ARG GIT_DOMAIN=https://github.com
ARG PIP_MIRROR=""
ARG AUDITWHEEL_VERSION=6.8.2

LABEL maintainer="a76yyyy <q981331502@163.com>"
LABEL org.opencontainers.image.source=https://github.com/qd-today/onnxruntime-docker

# 镜像源（国内构建用）：把 APK_MIRROR 末尾的 / 去掉后整体替换 host
RUN if [ -n "${APK_MIRROR}" ]; then \
        sed -i "s|https://dl-cdn.alpinelinux.org|${APK_MIRROR%/}|g" /etc/apk/repositories; \
    fi \
 && cat /etc/apk/repositories

# makedepends 来自 alpine/APKBUILD；patchelf 给 auditwheel 用；
# py3-pip 用来装 auditwheel（wheel 阶段的依赖收集 + RPATH 全交给它）
RUN apk add --no-cache \
        abseil-cpp-dev \
        bash \
        binutils \
        build-base \
        cmake \
        curl \
        gtest-dev \
        icu-dev \
        linux-headers \
        nlohmann-json \
        patch \
        patchelf \
        protobuf-dev \
        py3-numpy-dev \
        py3-packaging \
        py3-pip \
        py3-protobuf \
        py3-pybind11-dev \
        py3-setuptools \
        py3-wheel \
        python3 \
        python3-dev \
        re2-dev \
        samurai \
        zlib-dev

ARG PIP_MIRROR=""
ARG AUDITWHEEL_VERSION=6.8.2

RUN set -eux; \
    if [ -n "${PIP_MIRROR}" ]; then \
        pip_args="-i ${PIP_MIRROR}"; \
    else \
        pip_args=""; \
    fi; \
    python3 -m pip install --no-cache-dir --break-system-packages \
        ${pip_args} "auditwheel==${AUDITWHEEL_VERSION}"; \
    python3 -m auditwheel --version

WORKDIR /ort

# patch 配方直接复用 alpine/ 目录，不做任何修改
COPY alpine/ /ort/patches/

RUN set -eux; \
    mkdir -p /ort/src; \
    curl -fsSL --retry 5 --retry-delay 5 \
        "${GIT_DOMAIN%/}/microsoft/onnxruntime/archive/refs/tags/v${ONNX_VERSION}.tar.gz" \
      | tar -xz --strip-components=1 -C /ort/src; \
    test -f /ort/src/setup.py; \
    cat /ort/src/VERSION_NUMBER

# 按 APKBUILD 的 source= 顺序应用 patch（default_prepare 的等价操作）。
# flatbuffers-locale.patch 和 26187_*.patch 带 .noauto 后缀，由 prepare()/build()
# 单独处理，这里不打。
#
# 与 APKBUILD 的差异（两处，都是因为配方里的 patch 不是针对 1.24.4 的）：
#
# 1. 跳过 system.patch。它针对 1.22 及更早的 onnxruntime 写的（当时 absl /
#    protobuf / re2 都是无条件 FetchContent 拉源码），对 1.24.4 的
#    cmake/external/onnxruntime_external_deps.cmake 有 3/4 个 hunk 打不上。
#    1.24.4 上游已经改成 `onnxruntime_fetchcontent_declare(... FIND_PACKAGE_ARGS ...)`
#    「先 find_package，找不到才下载」，装了 abseil-cpp-dev / protobuf-dev /
#    re2-dev / nlohmann-json / gtest-dev 之后效果与 system.patch 一致（用系统库）。
#
# 2. 跳过 upb-fix.patch。它预先生成 protobuf::libupb / protoc-gen-upb* 这几个
#    IMPORTED 目标，本意是给「源码编译 protobuf」那条路补洞；但只要系统里能找到
#    protobuf，protobuf-config.cmake 自己就会定义这些目标，于是报
#    "Some (but not all) targets in this export set were already defined"。
RUN set -eux; \
    cd /ort/src; \
    for p in \
        no-execinfo.patch \
        0001-Remove-MATH_NO_EXCEPT-macro.patch \
        gcc-15.patch \
    ; do \
        echo ">>> applying ${p}"; \
        patch -p1 --forward --batch < "/ort/patches/${p}"; \
    done

########################################################################
# 2) cmake 配置
########################################################################
FROM src AS configured

ARG TARGETARCH

# APKBUILD: aarch64 需要关掉 Neural-Speed EP；armv7 需要 -Wno-overflow
RUN set -eux; \
    extra_cmake=""; \
    extra_cxx=""; \
    case "${TARGETARCH}" in \
        arm64)    extra_cmake="-Donnxruntime_USE_NEURAL_SPEED=OFF" ;; \
        arm|armv7) extra_cxx="-Wno-overflow" ;; \
    esac; \
    CFLAGS="-O2 -flto=auto" \
    CXXFLAGS="-O2 ${extra_cxx} -Wno-deprecated-declarations -Wno-error=template-id-cdtor -flto=auto -U_FORTIFY_SOURCE" \
    LDFLAGS="-Wl,--copy-dt-needed-entries" \
    cmake -S /ort/src/cmake -B /ort/src/build -G Ninja \
        -DCMAKE_BUILD_TYPE=None \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DBUILD_ONNX_PYTHON=ON \
        -Donnxruntime_BUILD_SHARED_LIB=ON \
        -Donnxruntime_BUILD_UNIT_TESTS=OFF \
        -Donnxruntime_ENABLE_PYTHON=ON \
        ${extra_cmake}

# APKBUILD 在 cmake 配置之后、编译之前打这个 patch（它 patch 的是
# build/_deps/flatbuffers-src 里的源码，所以必须在 FetchContent 之后）
RUN cd /ort/src && patch -p1 --forward --batch < /ort/patches/flatbuffers-locale.patch.noauto

########################################################################
# 3) 编译 + 打 wheel
########################################################################
FROM configured AS builder

RUN set -eux; \
    cmake --build /ort/src/build --parallel "$(nproc)"

# 注：APKBUILD 在 aarch64/loongarch64/ppc64le/riscv64 上还会打
# 26187_disable-hascpudevice-test.patch.noauto，它只是把 DeviceDiscoveryTest
# 里的一个 gtest 用例改名成 DISABLED_*。我们这个构建
# onnxruntime_BUILD_UNIT_TESTS=OFF，根本不编译该文件，因此这个 patch 跳过。
# 同理 0001-Remove-MATH_NO_EXCEPT-macro.patch / gcc-15.patch 也只是修测试代码，
# 这里仍然照配方打（无副作用），保持与 upstream 一致。

COPY pack_wheel.py /usr/local/bin/pack_wheel.py

# setup.py 的 package_data 只会在 build/ 目录里挑 libonnxruntime.so* 等文件，
# 所以先确认 cmake 的 POST_BUILD 已经把库拷到了 build/onnxruntime/capi/。
# bdist_wheel 完成后由 pack_wheel.py 做「自包含化 + 重新打包」。
RUN set -eux; \
    cd /ort/src/build; \
    ls -l onnxruntime/capi/*.so*; \
    python3 ../setup.py bdist_wheel --dist-dir /ort/wheel-raw; \
    ls -l /ort/wheel-raw

RUN set -eux; \
    python3 /usr/local/bin/pack_wheel.py \
        --wheel-dir /ort/wheel-raw \
        --out-dir /wheels \
        --lib-dir /ort/src/build/onnxruntime/capi; \
    cd /wheels; \
    ls -l; \
    sha256sum ./*.whl > SHA256SUMS; \
    cat SHA256SUMS

########################################################################
# 4) 产物出口（CI 用 --output type=local 取出）
########################################################################
FROM scratch AS wheel

COPY --from=builder /wheels/ /wheels/
