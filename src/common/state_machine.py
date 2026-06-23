"""Shared state-machine utilities."""


class StateMachine:
    """Placeholder for common phase and task state machines."""

    def step(self) -> None:
        """推进 StateMachine 一个处理周期。注意：输入输出约定需与上下游模块保持一致。"""
        raise NotImplementedError

