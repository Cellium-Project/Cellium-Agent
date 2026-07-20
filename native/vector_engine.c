#include "vector_engine.h"
#include <string.h>

#if defined(_M_X64) || defined(__x86_64__) || defined(_M_IX86) || defined(__i386__)
#define ARCH_X86 1
#ifdef _MSC_VER
#include <intrin.h>
#else
#include <cpuid.h>
#endif
#ifdef __AVX2__
#include <immintrin.h>
#endif
#elif defined(__aarch64__) || defined(_M_ARM64) || defined(__ARM_NEON) || defined(__ARM_NEON__)
#define ARCH_ARM 1
#include <arm_neon.h>
#endif

static int detect_avx2_fma(void) {
#if ARCH_X86
#ifdef _MSC_VER
    int info[4];
    __cpuidex(info, 7, 0);
    int has_avx2 = (info[1] >> 5) & 1;
    __cpuid(info, 1);
    int has_fma = (info[2] >> 12) & 1;
    return has_avx2 && has_fma;
#else
    unsigned int eax, ebx, ecx, edx;
    if (!__get_cpuid_count(7, 0, &eax, &ebx, &ecx, &edx))
        return 0;
    int has_avx2 = (ebx >> 5) & 1;
    if (!__get_cpuid(1, &eax, &ebx, &ecx, &edx))
        return 0;
    int has_fma = (ecx >> 12) & 1;
    return has_avx2 && has_fma;
#endif
#else
    return 0;
#endif
}

static int g_simd_level = -1;

static int get_simd_level(void) {
    if (g_simd_level < 0) {
#if ARCH_ARM
        g_simd_level = 1;
#elif ARCH_X86
        g_simd_level = detect_avx2_fma() ? 2 : 0;
#else
        g_simd_level = 0;
#endif
    }
    return g_simd_level;
}

VEC_API int vector_get_simd_level(void) {
    return get_simd_level();
}

static float dot_scalar(const float *a, const float *b, int dim) {
    float sum = 0.0f;
    int i = 0;
    int level = get_simd_level();

    if (level >= 2) {
#if ARCH_X86 && defined(__AVX2__)
        __m256 vsum = _mm256_setzero_ps();
        for (; i + 7 < dim; i += 8) {
            __m256 va = _mm256_loadu_ps(a + i);
            __m256 vb = _mm256_loadu_ps(b + i);
            vsum = _mm256_fmadd_ps(va, vb, vsum);
        }
        float tmp[8];
        _mm256_storeu_ps(tmp, vsum);
        for (int j = 0; j < 8; j++) sum += tmp[j];
#endif
    } else if (level >= 1) {
#if ARCH_ARM
        float32x4_t vsum = vdupq_n_f32(0.0f);
        for (; i + 3 < dim; i += 4) {
            float32x4_t va = vld1q_f32(a + i);
            float32x4_t vb = vld1q_f32(b + i);
            vsum = vmlaq_f32(vsum, va, vb);
        }
        sum += vgetq_lane_f32(vsum, 0) + vgetq_lane_f32(vsum, 1)
             + vgetq_lane_f32(vsum, 2) + vgetq_lane_f32(vsum, 3);
#endif
    }

    for (; i < dim; i++) {
        sum += a[i] * b[i];
    }
    return sum;
}

VEC_API float vector_dot(const float *a, const float *b, int dim) {
    return dot_scalar(a, b, dim);
}

VEC_API void vector_batch_dot(
    const float *query,
    const float *docs,
    int n,
    int dim,
    float *out_scores
) {
    for (int i = 0; i < n; i++) {
        out_scores[i] = dot_scalar(query, docs + (size_t)i * dim, dim);
    }
}
