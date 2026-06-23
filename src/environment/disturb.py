"""Disturbance manager.

Centralizes uncertainty index handling, stochastic configuration dispatch, and
runtime dynamic disturbance injection.
"""


class DisturbanceManager:
    """Manage stochastic and dynamic disturbances."""

    def tick(self, dt: float) -> None:
        """推进模块内部时钟或动态状态一个周期。注意：调用频率应与仿真步长一致。"""
        raise NotImplementedError

