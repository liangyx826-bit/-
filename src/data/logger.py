"""Key simulation data logging.

The architecture targets HDF5 for time-series persistence.
"""


class SimulationLogger:
    """Persist key simulation variables."""

    def write(self, record: dict[str, object]) -> None:
        """写入一条仿真记录。注意：具体落盘格式由 logger 实现决定。"""
        raise NotImplementedError

