"""One host per open-weight model, first-party fp8, no fallback: unpinned calls were spread over hosts incl. fp4 ones, and on OpenInference fp4
GLM 5.3 Flash looped until truncated in 85 of 447 everyday votes (vs 1 of 320 on Relace). Anthropic models are not pinned (first-party weights only).
-- PI/Claude, 2026-10-12"""
PIN = {'z-ai/glm-5.3-flash': 'z-ai/fp8', 'z-ai/glm-5.3': 'z-ai/fp8', 'xiaomi/mimo-v2.6-flash': 'xiaomi/fp8', 'xiaomi/mimo-v2.6-pro': 'xiaomi/fp8'}


def provider(model):
    host = PIN.get(model.split(':')[0])
    return {'provider': {'only': [host], 'allow_fallbacks': False, 'require_parameters': True}} if host else {}
