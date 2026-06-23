"""Algorithm base types and message schema declaration API."""


class AlgorithmBase:
    """Base class for coordination and node algorithm plugins."""

    def declare_message_schema(self) -> dict[str, object]:
        """声明算法需要收发的消息结构。注意：返回内容是通信层约定，字段名变更需同步上下游。"""
        raise NotImplementedError

