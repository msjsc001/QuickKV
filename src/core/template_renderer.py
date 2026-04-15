# -*- coding: utf-8 -*-
import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from liquid import Environment, Mode


class TemplateRenderError(Exception):
    """模板解析或渲染失败时抛出的人类可读错误。"""


@dataclass
class TemplateInputField:
    key: str
    label: str
    default: str = ""


@dataclass
class PreparedTemplate:
    original_text: str
    liquid_source: str
    input_fields: list


class TemplateRenderer:
    """将 QuickKV 的中文模板语法解析为可执行的安全模板。"""

    STATEMENT_PATTERN = re.compile(r"{{\s*(.*?)\s*}}", re.DOTALL)
    SUPPORTED_VARIABLES = {"今天", "现在", "剪贴板"}
    SUPPORTED_FILTERS = {"默认", "格式化", "大写", "小写", "去空格"}

    def __init__(self):
        self.environment = Environment(
            tolerance=Mode.STRICT,
            strict_filters=True,
            autoescape=False,
        )
        self.environment.add_filter("默认", self._filter_default)
        self.environment.add_filter("格式化", self._filter_format)
        self.environment.add_filter("大写", self._filter_upper)
        self.environment.add_filter("小写", self._filter_lower)
        self.environment.add_filter("去空格", self._filter_trim)

    def contains_template(self, text):
        return bool(text) and "{{" in text and "}}" in text

    def prepare(self, text):
        if not self.contains_template(text):
            return PreparedTemplate(text, text, [])

        matches = list(self.STATEMENT_PATTERN.finditer(text))
        if not matches:
            raise TemplateRenderError("模板语法不完整，请检查 {{ 和 }} 是否成对出现。")

        rebuilt_parts = []
        input_registry = {}
        cursor = 0

        for match in matches:
            rebuilt_parts.append(text[cursor:match.start()])
            expression = match.group(1).strip()
            rebuilt_parts.append(f"{{{{ {self._transform_expression(expression, input_registry)} }}}}")
            cursor = match.end()

        rebuilt_parts.append(text[cursor:])
        liquid_source = "".join(rebuilt_parts)

        try:
            self.environment.from_string(liquid_source)
        except Exception as e:
            raise TemplateRenderError(f"模板语法错误：{self._compact_error(e)}")

        input_fields = sorted(input_registry.values(), key=lambda field: int(field.key.split("_")[-1]))
        return PreparedTemplate(text, liquid_source, input_fields)

    def render(self, prepared_template, input_values=None, clipboard_text=""):
        input_values = input_values or {}
        now_value = datetime.now()
        context = {
            "today_text": date.today().isoformat(),
            "today_value": date.today(),
            "now_text": now_value.strftime("%Y-%m-%d %H:%M"),
            "now_value": now_value,
            "clipboard": clipboard_text or "",
        }

        for field in prepared_template.input_fields:
            context[field.key] = input_values.get(field.key, "")

        try:
            template = self.environment.from_string(prepared_template.liquid_source)
            return template.render(**context)
        except TemplateRenderError:
            raise
        except Exception as e:
            raise TemplateRenderError(f"模板渲染失败：{self._compact_error(e)}")

    def _transform_expression(self, expression, input_registry):
        raw_segments = expression.split("|")
        if any(not segment.strip() for segment in raw_segments):
            raise TemplateRenderError(f"模板表达式写法不正确：{expression}")

        segments = [segment.strip() for segment in raw_segments]
        base_kind, internal_base, field = self._parse_base_segment(segments[0], input_registry)

        has_format_filter = False
        transformed_filters = []

        for filter_segment in segments[1:]:
            filter_name, filter_arg = self._parse_filter_segment(filter_segment)
            if filter_name not in self.SUPPORTED_FILTERS:
                raise TemplateRenderError(f"未识别过滤器：{filter_name}")

            if filter_name == "格式化":
                if base_kind not in {"today", "now"}:
                    raise TemplateRenderError("“格式化”只能用于 {{今天}} 或 {{现在}}。")
                has_format_filter = True
                if not filter_arg:
                    raise TemplateRenderError("“格式化”缺少格式内容。")
            elif filter_name == "默认":
                if not filter_arg:
                    raise TemplateRenderError("“默认”缺少默认值。")
                if field and not field.default:
                    field.default = filter_arg
            elif filter_arg:
                raise TemplateRenderError(f"过滤器“{filter_name}”不接受额外参数。")

            transformed_filters.append(self._build_filter_expression(filter_name, filter_arg))

        if base_kind == "today":
            internal_base = "today_value" if has_format_filter else "today_text"
        elif base_kind == "now":
            internal_base = "now_value" if has_format_filter else "now_text"

        if not transformed_filters:
            return internal_base
        return " | ".join([internal_base] + transformed_filters)

    def _parse_base_segment(self, base_segment, input_registry):
        if base_segment in self.SUPPORTED_VARIABLES:
            if base_segment == "今天":
                return "today", "today_text", None
            if base_segment == "现在":
                return "now", "now_text", None
            return "clipboard", "clipboard", None

        if base_segment.startswith("输入:"):
            label = base_segment[3:].strip()
            if not label:
                raise TemplateRenderError("输入变量写法不正确，请使用例如 {{输入:项目名}} 的形式。")
            field = input_registry.get(label)
            if not field:
                field = TemplateInputField(
                    key=f"input_{len(input_registry)}",
                    label=label,
                    default="",
                )
                input_registry[label] = field
            return "input", field.key, field

        raise TemplateRenderError(f"未识别变量：{base_segment}")

    def _parse_filter_segment(self, filter_segment):
        if ":" not in filter_segment:
            return filter_segment.strip(), None

        filter_name, raw_arg = filter_segment.split(":", 1)
        filter_name = filter_name.strip()
        raw_arg = raw_arg.strip()
        if not filter_name:
            raise TemplateRenderError(f"过滤器写法不正确：{filter_segment}")
        return filter_name, self._normalize_filter_arg(raw_arg)

    def _normalize_filter_arg(self, raw_arg):
        if not raw_arg:
            return ""
        if len(raw_arg) >= 2 and raw_arg[0] == raw_arg[-1] and raw_arg[0] in {"'", '"'}:
            return raw_arg[1:-1]
        return raw_arg

    def _build_filter_expression(self, filter_name, filter_arg):
        if filter_arg is None:
            return filter_name
        return f"{filter_name}: {json.dumps(filter_arg, ensure_ascii=False)}"

    def _compact_error(self, error):
        return " ".join(str(error).split())

    def _filter_default(self, value, default_value=""):
        if value is None:
            return default_value
        if isinstance(value, str) and value == "":
            return default_value
        return value

    def _filter_format(self, value, format_string):
        if isinstance(value, (date, datetime)):
            return value.strftime(format_string)
        raise TemplateRenderError("“格式化”只能用于 {{今天}} 或 {{现在}}。")

    def _filter_upper(self, value):
        return str(value).upper()

    def _filter_lower(self, value):
        return str(value).lower()

    def _filter_trim(self, value):
        return str(value).strip()
