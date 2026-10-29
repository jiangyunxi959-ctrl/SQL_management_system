"""公式错误。位置使用原文的字符下标，start 含、end 不含。"""


class FactorError(Exception):
    def __init__(self, category: str, message: str, start: int, end: int):
        self.category = category
        self.message = message
        self.start = max(0, start)
        self.end = max(self.start, end)
        super().__init__(f"{message}（位置 {self.start}:{self.end}）")
