"""System Prompt Manager - 支持动态切换和追加system prompt模板"""

from __future__ import annotations

from pathlib import Path

from common.io_utils import read_text
from common.path_utils import resolve_from_file


class SystemPromptManager:
    """管理system prompt模板的加载、切换和追加"""

    def __init__(self, base_prompt_path: str, base_dir: Path):
        """
        初始化System Prompt管理器

        Args:
            base_prompt_path: 基础prompt模板路径（相对或绝对路径）
            base_dir: 基准目录，用于解析相对路径
        """
        self.base_dir = base_dir
        self.base_prompt_path = base_prompt_path
        self.current_prompt = self._load_prompt(base_prompt_path)
        self.prompt_history = [
            {
                "source": "base",
                "path": base_prompt_path,
                "mode": "initial",
                "content_length": len(self.current_prompt),
            }
        ]

    def _load_prompt(self, prompt_path: str) -> str:
        """
        从文件加载prompt内容

        Args:
            prompt_path: prompt文件路径（相对或绝对路径）

        Returns:
            prompt文本内容
        """
        resolved_path = resolve_from_file(prompt_path, self.base_dir)
        content = read_text(resolved_path).strip()
        if not content:
            raise ValueError(f"System prompt file is empty: {resolved_path}")
        return content

    def apply_switch(self, switch_config: dict) -> dict:
        """
        应用prompt切换配置

        Args:
            switch_config: 切换配置字典，包含：
                - switch_to: 目标prompt文件路径
                - mode: "replace" 或 "append"
                - after_user_input: 在第几个用户输入后切换（用于记录）

        Returns:
            切换记录字典
        """
        target_path = switch_config["switch_to"]
        mode = switch_config.get("mode", "replace")
        after_user_input = switch_config.get("after_user_input", -1)

        # 加载新的prompt内容
        new_content = self._load_prompt(target_path)

        # 应用切换
        previous_prompt = self.current_prompt
        if mode == "replace":
            self.current_prompt = new_content
        elif mode == "append":
            self.current_prompt = f"{self.current_prompt}\n\n{new_content}"
        else:
            raise ValueError(
                f"Invalid switch mode: {mode}. Must be 'replace' or 'append'"
            )

        # 记录切换历史
        switch_record = {
            "source": "switch",
            "path": target_path,
            "mode": mode,
            "after_user_input": after_user_input,
            "content_length": len(self.current_prompt),
            "previous_length": len(previous_prompt),
        }
        self.prompt_history.append(switch_record)

        return switch_record

    def get_current_prompt(self) -> str:
        """获取当前的system prompt内容"""
        return self.current_prompt

    def get_prompt_history(self) -> list[dict]:
        """获取prompt切换历史记录"""
        return self.prompt_history.copy()

    def get_base_prompt(self) -> str:
        """获取初始的base prompt内容"""
        return self._load_prompt(self.base_prompt_path)

    def reset_to_base(self) -> None:
        """重置为初始的base prompt"""
        self.current_prompt = self.get_base_prompt()
        self.prompt_history.append(
            {
                "source": "reset",
                "path": self.base_prompt_path,
                "mode": "reset",
                "content_length": len(self.current_prompt),
            }
        )
