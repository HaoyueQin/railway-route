"""Web 前端文件结构契约 + 静态文件服务测试。

覆盖：
- web/index.html 关键元素 id、资源引用、无内联事件（XSS 契约）；
- web/styles.css 关键样式类；
- web/app.js 关键函数与状态结构；
- src/main.py APIHandler 对 /、/styles.css、/app.js 的静态服务与 404。
"""

import io
import re
import unittest
from pathlib import Path

from src.main import APIHandler

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# 正则字面量的合法前缀（前一个非空白字符属于该集合时，/ 视为正则而非除号）
_REGEX_PREFIX = set("(=,:[!&|?{};")

# 值类 token（字符串/数字/非关键字标识符）——连续两个值 token 是 JS 语法错误
_VALUE_TOKENS = {"str", "num", "id"}

# JS 关键字/保留字：允许合法地紧跟在另一个标识符之后，不算"值 token"
_JS_KEYWORDS = {
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "enum", "export", "extends", "false",
    "finally", "for", "function", "if", "import", "in", "instanceof", "let",
    "new", "null", "of", "return", "static", "super", "switch", "this",
    "throw", "true", "try", "typeof", "var", "void", "while", "with",
    "yield", "async", "await", "get", "set",
}


def _is_value_token(tok: tuple[str, str, int]) -> bool:
    t, content, _ = tok
    if t in ("str", "num"):
        return True
    if t == "id":
        return content not in _JS_KEYWORDS
    return False


def js_tokens(js: str) -> list[tuple[str, str, int]]:
    """极简 JS 词法扫描，仅用于捕获 HTML 片段拼接中的引号错误。

    返回 [(类型, 内容, 行号)]，类型为 str/id/num/op；注释被跳过；
    正则字面量仅在 _REGEX_PREFIX 前缀后识别。不做完整语法分析。
    """
    tokens = []
    i, n, line = 0, len(js), 1
    while i < n:
        c = js[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n:
            nxt = js[i + 1]
            if nxt == "/":  # 行注释
                while i < n and js[i] != "\n":
                    i += 1
                continue
            if nxt == "*":  # 块注释
                i += 2
                while i + 1 < n and not (js[i] == "*" and js[i + 1] == "/"):
                    if js[i] == "\n":
                        line += 1
                    i += 1
                i += 2
                continue
            prev = tokens[-1][1] if tokens else ""
            if prev in _REGEX_PREFIX or not tokens:  # 正则字面量
                j = i + 1
                while j < n and js[j] not in "/\n":
                    if js[j] == "\\":
                        j += 1
                    j += 1
                j += 1  # 收尾 /
                while j < n and js[j].isalpha():  # 标志位 g/i/m
                    j += 1
                tokens.append(("op", js[i:j], line))
                i = j
                continue
        if c in "'\"`":
            quote = c
            start_line = line
            j = i + 1
            closed = False
            while j < n:
                ch = js[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    closed = True
                    break
                if ch == "\n" and quote != "`":
                    break  # 未闭合字符串
                j += 1
            if not closed:
                raise AssertionError(
                    f"app.js 第 {start_line} 行存在未闭合的字符串字面量（引号配对错误）"
                )
            tokens.append(("str", js[i + 1:j], start_line))
            i = j + 1
            continue
        if c.isalpha() or c == "_" or c == "$":
            j = i
            while j < n and (js[j].isalnum() or js[j] in "_$"):
                j += 1
            tokens.append(("id", js[i:j], line))
            i = j
            continue
        if c.isdigit():
            j = i
            while j < n and (js[j].isalnum() or js[j] in "._"):
                j += 1
            tokens.append(("num", js[i:j], line))
            i = j
            continue
        tokens.append(("op", c, line))
        i += 1
    tokens.append(("eof", "", line))
    return tokens


def http_get(path: str) -> bytes:
    """直接调用 APIHandler.do_GET，绕过 socket，返回完整响应字节。"""
    handler = object.__new__(APIHandler)
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO(b"")
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.path = path
    handler.do_GET()
    return handler.wfile.getvalue()


class WebHtmlContractTest(unittest.TestCase):
    def setUp(self):
        self.html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_required_element_ids_present(self):
        for element_id in [
            "from", "to", "from-sugg", "to-sugg", "from-mode", "to-mode", "btn-swap",
            "dd-search-profile",
            "adv-toggle", "adv-panel",
            "dep-after", "dep-before", "arr-after", "arr-before",
            "same-wheel", "inter-wheel",
            "max-rng", "max-num", "xfer-at",
            "btn-search", "results",
        ]:
            self.assertIn(f'id="{element_id}"', self.html, f"缺少元素 id={element_id}")

    def test_match_mode_now_per_end_buttons(self):
        # 重构后匹配模式由每端"全部站/本站"按钮独立控制，全局下拉已移除
        self.assertNotIn('id="dd-match-mode"', self.html)
        self.assertIn('id="from-mode"', self.html)
        self.assertIn('id="to-mode"', self.html)

    def test_static_assets_referenced(self):
        self.assertIn('href="/styles.css"', self.html)
        self.assertIn('src="/app.js"', self.html)

    def test_no_native_select_in_form(self):
        # 步骤2 契约：表单不再使用原生 <select>，杜绝原生白色下拉面板
        self.assertNotIn("<select", self.html)

    def test_no_inline_event_handlers(self):
        # 步骤4/7 契约：事件全部由 app.js 绑定，不依赖内联 onclick（规避 XSS）
        self.assertNotIn("onclick=", self.html)
        self.assertNotIn("oninput=", self.html)


class WebCssContractTest(unittest.TestCase):
    def setUp(self):
        self.css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    def test_required_classes_present(self):
        for cls in [
            ".cdd", ".cdd-btn", ".cdd-panel", ".cdd-opt",   # 自定义下拉
            ".rc-flow", ".rc-node", ".rc-node-name", ".rc-arrow-node", ".rc-arrow-code",  # 路线流
            ".rc-detail", ".tt-tbl", ".tt-gnd",             # 展开详情
            ".sf-bar", ".sf-clear",                          # 排序筛选
            ".hero", ".cd", ".rc-main", ".meta", ".empty",
        ]:
            self.assertIn(cls, self.css, f"缺少样式类 {cls}")

    def test_cdd_panel_is_glass_not_opaque_white(self):
        # 步骤2 契约：下拉面板使用半透明玻璃背景，不再依赖原生白色渲染
        panel_block = self.css.split(".cdd-panel{")[1].split("}")[0]
        self.assertIn("rgba(", panel_block)
        self.assertIn("backdrop-filter", panel_block)

    def test_z_top_escape_stack_context(self):
        # 回归防护：backdrop-filter 卡片会困住绝对定位下拉面板，
        # 展开时必须提升宿主卡片/结果区的 z-index，否则选项被后续卡片覆盖
        self.assertIn(".cd.z-top", self.css)
        self.assertIn("#results.z-top", self.css)
        self.assertIn("z-index:300", self.css)


class WebJsContractTest(unittest.TestCase):
    def setUp(self):
        self.js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    def test_required_functions_present(self):
        for fn in [
            "buildDropdown", "suggest", "search",
            "render", "renderList", "renderGroup",
            "buildRouteFlow", "buildTimetable", "closeDropdowns",
            "setDropdownState",
        ]:
            self.assertIn(f"function {fn}", self.js, f"缺少函数 {fn}")

    def test_z_top_lift_used_in_js(self):
        # 回归防护：下拉展开时必须调用 z-top 提升，防止面板被兄弟卡片覆盖
        self.assertIn("z-top", self.js)
        self.assertIn('closest(".cd")', self.js)
        self.assertIn('closest("#results")', self.js)

    def test_dropdown_tracks_mutable_current_value(self):
        # 回归防护：选项回调必须比较可变 current，否则选中后无法切回初始选项
        self.assertIn("let current = value", self.js)
        self.assertIn("o.value !== current", self.js)

    def test_sort_and_filter_state_is_js_variable(self):
        # 步骤6 契约：排序筛选状态存于 _sf，不依赖 DOM 回读，切换不丢状态
        self.assertIn("const _sf", self.js)
        self.assertIn('sort: "score"', self.js)
        self.assertIn('xfer: "all"', self.js)
        self.assertIn("city: \"\"", self.js)

    def test_sort_options_cover_six_dimensions(self):
        for label in ["综合评分", "总耗时", "总里程", "出发时间", "到达时间", "换乘次数"]:
            self.assertIn(label, self.js)

    def test_xfer_filter_options(self):
        for label in ["仅直达", "仅同站换乘", "含异站换乘"]:
            self.assertIn(label, self.js)

    def test_no_settimeout_dropdown_wrapping(self):
        # 步骤2 契约：不再依赖 setTimeout 延迟包装原生 select / 下拉开合
        self.assertNotIn("setTimeout(() => closeDropdowns", self.js)
        self.assertNotIn("setTimeout(() => setDropdownState", self.js)

    def test_string_literal_quote_balance(self):
        # 回归防护：双引号字符串内嵌未转义双引号会破坏整个脚本解析
        # （HTML 片段拼接常见错误，曾导致 app.js 整体失效）
        tokens = js_tokens(self.js)
        prev = None
        for tok in tokens:
            if prev and _is_value_token(prev) and _is_value_token(tok):
                self.fail(
                    f"app.js 第 {tok[2]} 行：值 token 相邻（{prev[1]!r} 后紧跟 {tok[1]!r}），"
                    "疑似字符串引号配对错误"
                )
            prev = tok


class WebStaticServingTest(unittest.TestCase):
    def test_index_served(self):
        resp = http_get("/")
        self.assertIn(b"HTTP/1.0 200 OK", resp)
        self.assertIn(b"text/html", resp)
        self.assertIn("铁路出行路径规划".encode("utf-8"), resp)

    def test_styles_served(self):
        resp = http_get("/styles.css")
        self.assertIn(b"HTTP/1.0 200 OK", resp)
        self.assertIn(b"text/css", resp)
        self.assertIn(b".cdd-panel", resp)

    def test_app_js_served(self):
        resp = http_get("/app.js")
        self.assertIn(b"HTTP/1.0 200 OK", resp)
        self.assertIn(b"application/javascript", resp)
        self.assertIn(b"buildRouteFlow", resp)

    def test_unknown_path_404(self):
        resp = http_get("/nope.js")
        self.assertIn(b"HTTP/1.0 404", resp)


if __name__ == "__main__":
    unittest.main()
