"""流水线公共异常。"""


class ProviderError(RuntimeError):
    """调用模型失败（网络、鉴权、拒答、输出不合规等），上层据此把提交标记为失败或降级。"""
