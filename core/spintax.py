# -*- coding: utf-8 -*-
"""
Spintax engine: sinh các biến thể nội dung từ cú pháp {a|b|{c|d}}.
Hỗ trợ lồng nhau và thay biến {group_name}, {group_id}, ...
"""
import random


class _Text:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def render(self, rng):
        return self.text

    def count(self):
        return 1


class _Choice:
    __slots__ = ("options",)

    def __init__(self, options):
        self.options = options

    def render(self, rng):
        return rng.choice(self.options).render(rng)

    def count(self):
        return sum(o.count() for o in self.options)


class _Seq:
    __slots__ = ("children",)

    def __init__(self, children):
        self.children = children

    def render(self, rng):
        return "".join(c.render(rng) for c in self.children)

    def count(self):
        total = 1
        for c in self.children:
            total *= c.count()
        return total


class Spintax:
    """Bộ sinh biến thể spintax."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    # ---------------- API chính ----------------
    def render(self, text, variables=None):
        """Render 1 biến thể ngẫu nhiên, thay biến trước khi spin."""
        node = self._parse(self._substitute(text, variables))
        return node.render(self.rng)

    def variants(self, text, n=10, variables=None):
        """Trả về tối đa n biến thể khác nhau."""
        node = self._parse(self._substitute(text, variables))
        total = node.count()
        out, seen = [], set()
        for _ in range(max(n * 10, 10)):
            s = node.render(self.rng)
            if s not in seen:
                seen.add(s)
                out.append(s)
            if len(out) >= min(n, total):
                break
        return out

    def count(self, text, variables=None):
        """Đếm tổng số biến thể có thể sinh ra."""
        node = self._parse(self._substitute(text, variables))
        return node.count()

    # ---------------- nội bộ ----------------
    @staticmethod
    def _substitute(text, variables):
        if variables:
            for key, value in variables.items():
                text = text.replace("{%s}" % key, str(value))
        return text

    def _parse(self, text):
        node, _ = self._parse_seq(text, 0, 0)
        return node

    def _parse_seq(self, s, i, depth):
        children, buf = [], []
        while i < len(s):
            c = s[i]
            if c == "{":
                if buf:
                    children.append(_Text("".join(buf)))
                    buf = []
                options, i = self._parse_choice(s, i + 1, depth + 1)
                children.append(_Choice(options))
            elif (c == "}" or c == "|") and depth > 0:
                break
            else:
                buf.append(c)
                i += 1
        if buf:
            children.append(_Text("".join(buf)))
        return _Seq(children), i

    def _parse_choice(self, s, i, depth):
        options = []
        while True:
            node, i = self._parse_seq(s, i, depth)
            options.append(node)
            if i < len(s) and s[i] == "|":
                i += 1
                continue
            break
        if i < len(s) and s[i] == "}":
            i += 1
        return options, i
