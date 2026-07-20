#ifndef VECTOR_ENGINE_H
#define VECTOR_ENGINE_H

#ifdef _WIN32
#define VEC_API __declspec(dllexport)
#else
#define VEC_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

VEC_API void vector_batch_dot(
    const float *query,
    const float *docs,
    int n,
    int dim,
    float *out_scores
);

VEC_API float vector_dot(const float *a, const float *b, int dim);

VEC_API int vector_get_simd_level(void);

#ifdef __cplusplus
}
#endif

#endif
