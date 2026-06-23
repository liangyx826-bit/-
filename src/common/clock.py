"""Simulation clock utilities."""


class SimulationClock:
    """Track simulation time."""

    def __init__(self) -> None:
        """初始化 SimulationClock 实例，建立后续运行所需状态。注意：构造阶段不应启动耗时流程。"""
        self.time = 0.0

    def tick(self, dt: float) -> float:
        """推进模块内部时钟或动态状态一个周期。注意：调用频率应与仿真步长一致。"""
        self.time += dt
        return self.time

