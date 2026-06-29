"""Spintax resolver: {вариант1|вариант2|...} with nesting and escaping."""

from __future__ import annotations

import random


class SpintaxNode:
    def resolve(self) -> str:
        raise NotImplementedError()


class SpintaxText(SpintaxNode):
    def __init__(self, text: str):
        self.text = text

    def resolve(self) -> str:
        return self.text


class SpintaxChoice(SpintaxNode):
    def __init__(self):
        self.options: list[list[SpintaxNode]] = [[]]

    def add_to_current(self, node: SpintaxNode):
        self.options[-1].append(node)

    def new_option(self):
        self.options.append([])

    def resolve(self) -> str:
        if not self.options or not self.options[0]:
            return ""
        chosen = random.choice(self.options)
        return "".join(node.resolve() for node in chosen)


def resolve_spintax(text: str) -> str:
    """Resolves spintax like {option1|option2|option3}.

    Supports nesting, e.g. {Hi|Hello {friend|colleague}}.
    Supports escaping: \\{, \\}, \\| and \\\\.
    """
    if not text:
        return ""

    root: list[SpintaxNode] = []
    stack: list[SpintaxChoice] = []
    current_list: list[SpintaxNode] = root

    i = 0
    n = len(text)
    current_text: list[str] = []

    def flush_text():
        if current_text:
            current_list.append(SpintaxText("".join(current_text)))
            current_text.clear()

    while i < n:
        char = text[i]
        if char == "\\":
            if i + 1 < n:
                next_char = text[i + 1]
                if next_char in ("{", "}", "|", "\\"):
                    current_text.append(next_char)
                    i += 2
                    continue
            current_text.append(char)
            i += 1
        elif char == "{":
            flush_text()
            node = SpintaxChoice()
            stack.append(node)
            current_list = node.options[0]
            i += 1
        elif char == "}":
            flush_text()
            if stack:
                node = stack.pop()
                current_list = stack[-1].options[-1] if stack else root
                current_list.append(node)
            else:
                current_text.append(char)
            i += 1
        elif char == "|":
            flush_text()
            if stack:
                stack[-1].new_option()
                current_list = stack[-1].options[-1]
            else:
                current_text.append(char)
            i += 1
        else:
            current_text.append(char)
            i += 1

    flush_text()

    while stack:
        node = stack.pop()
        if stack:
            stack[-1].options[-1].append(node)
        else:
            root.append(node)

    return "".join(node.resolve() for node in root)
