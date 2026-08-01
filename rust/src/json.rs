//! 极简 JSON 解析器（零依赖，仅服务对拍基准文件 m2_baseline.json）。
//!
//! 覆盖本仓库 dump 脚本生成的全部形态：对象/数组/字符串/数字/bool/null。
//! 不序列化；M4 引入 HTTP 栈时再评估 serde_json 等正式依赖。

use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Number(f64),
    String(String),
    Array(Vec<Json>),
    Object(HashMap<String, Json>),
}

struct Parser<'a> {
    chars: std::iter::Peekable<std::str::Chars<'a>>,
}

pub fn parse(s: &str) -> Result<Json, String> {
    let mut p = Parser { chars: s.chars().peekable() };
    let v = p.parse_value()?;
    p.skip_ws();
    if p.chars.peek().is_some() {
        return Err("JSON 尾部有残留内容".into());
    }
    Ok(v)
}

impl<'a> Parser<'a> {
    fn skip_ws(&mut self) {
        while self.chars.peek().is_some_and(|c| c.is_whitespace()) {
            self.chars.next();
        }
    }

    fn expect(&mut self, ch: char) -> Result<(), String> {
        match self.chars.next() {
            Some(c) if c == ch => Ok(()),
            other => Err(format!("期望 '{ch}'，实际 {other:?}")),
        }
    }

    fn parse_value(&mut self) -> Result<Json, String> {
        self.skip_ws();
        match self.chars.peek() {
            Some('{') => self.parse_object(),
            Some('[') => self.parse_array(),
            Some('"') => Ok(Json::String(self.parse_string()?)),
            Some('t') => self.parse_literal("true", Json::Bool(true)),
            Some('f') => self.parse_literal("false", Json::Bool(false)),
            Some('n') => self.parse_literal("null", Json::Null),
            Some(_) => self.parse_number(),
            None => Err("JSON 意外结束".into()),
        }
    }

    fn parse_literal(&mut self, lit: &str, value: Json) -> Result<Json, String> {
        for ch in lit.chars() {
            if self.chars.next() != Some(ch) {
                return Err(format!("非法字面量，期望 {lit}"));
            }
        }
        Ok(value)
    }

    fn parse_string(&mut self) -> Result<String, String> {
        self.expect('"')?;
        let mut out = String::new();
        loop {
            match self.chars.next() {
                None => return Err("字符串未闭合".into()),
                Some('"') => return Ok(out),
                Some('\\') => match self.chars.next() {
                    Some('"') => out.push('"'),
                    Some('\\') => out.push('\\'),
                    Some('/') => out.push('/'),
                    Some('b') => out.push('\u{8}'),
                    Some('f') => out.push('\u{c}'),
                    Some('n') => out.push('\n'),
                    Some('r') => out.push('\r'),
                    Some('t') => out.push('\t'),
                    Some('u') => {
                        let mut code = 0u32;
                        for _ in 0..4 {
                            let hex = self.chars.next().ok_or("\\u 转义不完整")?;
                            code = code * 16 + hex.to_digit(16).ok_or("\\u 非法十六进制")?;
                        }
                        out.push(char::from_u32(code).ok_or("\\u 非法码点")?);
                    }
                    other => return Err(format!("非法转义 {other:?}")),
                },
                Some(c) => out.push(c),
            }
        }
    }

    fn parse_number(&mut self) -> Result<Json, String> {
        let mut buf = String::new();
        while self.chars.peek().is_some_and(|c| c.is_ascii_digit() || "+-.eE".contains(*c)) {
            buf.push(self.chars.next().unwrap());
        }
        if buf.is_empty() {
            return Err("非法数字".into());
        }
        buf.parse::<f64>()
            .map(Json::Number)
            .map_err(|_| format!("非法数字 {buf}"))
    }

    fn parse_array(&mut self) -> Result<Json, String> {
        self.expect('[')?;
        let mut items = Vec::new();
        self.skip_ws();
        if self.chars.peek() == Some(&']') {
            self.chars.next();
            return Ok(Json::Array(items));
        }
        loop {
            items.push(self.parse_value()?);
            self.skip_ws();
            match self.chars.next() {
                Some(',') => continue,
                Some(']') => return Ok(Json::Array(items)),
                other => return Err(format!("数组期望 , 或 ]，实际 {other:?}")),
            }
        }
    }

    fn parse_object(&mut self) -> Result<Json, String> {
        self.expect('{')?;
        let mut map = HashMap::new();
        self.skip_ws();
        if self.chars.peek() == Some(&'}') {
            self.chars.next();
            return Ok(Json::Object(map));
        }
        loop {
            self.skip_ws();
            let key = self.parse_string()?;
            self.skip_ws();
            self.expect(':')?;
            let value = self.parse_value()?;
            map.insert(key, value);
            self.skip_ws();
            match self.chars.next() {
                Some(',') => continue,
                Some('}') => return Ok(Json::Object(map)),
                other => return Err(format!("对象期望 , 或 }}，实际 {other:?}")),
            }
        }
    }
}

// ── 便捷取值 ─────────────────────────────────────────

impl Json {
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Number(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Json::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&Vec<Json>> {
        match self {
            Json::Array(a) => Some(a),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&HashMap<String, Json>> {
        match self {
            Json::Object(o) => Some(o),
            _ => None,
        }
    }

    /// 序列化为 JSON 文本（ensure_ascii=False：UTF-8 直出；数字整数显示整数）。
    pub fn to_string(&self) -> String {
        let mut out = String::new();
        self.write_to(&mut out);
        out
    }

    fn write_to(&self, out: &mut String) {
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Json::Number(n) => {
                if n.fract() == 0.0 && n.abs() < 9.0e15 {
                    out.push_str(&format!("{}", *n as i64));
                } else {
                    out.push_str(&format!("{n}"));
                }
            }
            Json::String(s) => {
                out.push('"');
                for c in s.chars() {
                    match c {
                        '"' => out.push_str("\\\""),
                        '\\' => out.push_str("\\\\"),
                        '\n' => out.push_str("\\n"),
                        '\r' => out.push_str("\\r"),
                        '\t' => out.push_str("\\t"),
                        c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
                        c => out.push(c),
                    }
                }
                out.push('"');
            }
            Json::Array(a) => {
                out.push('[');
                for (i, v) in a.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    v.write_to(out);
                }
                out.push(']');
            }
            Json::Object(o) => {
                out.push('{');
                for (i, (k, v)) in o.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    Json::String(k.clone()).write_to(out);
                    out.push(':');
                    v.write_to(out);
                }
                out.push('}');
            }
        }
    }
}
