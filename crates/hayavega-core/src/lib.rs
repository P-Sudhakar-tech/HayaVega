//! Framework primitives intentionally independent of Python bindings.

pub fn normalize_path(path: &str) -> String {
    let trimmed = path.trim();
    if trimmed.is_empty() || trimmed == "/" {
        return "/".to_owned();
    }
    format!("/{}", trimmed.trim_matches('/'))
}

/// Escape a string the same way Python's `html.escape(value, quote=True)` does
/// (the call `templates.py` makes for every `{{ }}` interpolation).
pub fn escape_html(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '&' => output.push_str("&amp;"),
            '<' => output.push_str("&lt;"),
            '>' => output.push_str("&gt;"),
            '"' => output.push_str("&quot;"),
            '\'' => output.push_str("&#x27;"),
            other => output.push(other),
        }
    }
    output
}

/// Render the exact HTML `benchmarks/hayavega_app/templates/page.html` produces
/// for a list of (title, views) rows -- a hand-compiled, best-case stand-in
/// for what a native Rust template engine could produce for this one
/// template, used only to measure whether such an engine is worth building
/// at all before anyone invests in a general one. Not a template engine
/// itself: this function knows nothing about the template file's syntax.
pub fn render_post_list_page(posts: &[(String, i64)]) -> String {
    let mut output = String::from("<!doctype html>\n<html>\n<head><title>Posts</title></head>\n<body>\n<h1>Posts</h1>\n<ul>\n");
    for (title, views) in posts {
        output.push_str("\n<li>");
        output.push_str(&escape_html(title));
        output.push_str(" (");
        output.push_str(&views.to_string());
        output.push_str(" views)</li>\n");
    }
    output.push_str("\n</ul>\n</body>\n</html>\n");
    output
}

/// Render the exact, compact `<ul><li>...</li>...</ul>` markup
/// `hayavega.ui`'s `ul(*(li(...) for ...))` component builders produce for a
/// list of (title, views) rows -- a hand-compiled, best-case stand-in for a
/// native Rust renderer of one specific htmx-swapped fragment shape.
/// Benchmark-only; see `benchmarks/native_htmx_fragment.py`.
pub fn render_fragment_items(items: &[(String, i64)]) -> String {
    let mut output = String::from("<ul>");
    for (title, views) in items {
        output.push_str("<li>");
        output.push_str(&escape_html(title));
        output.push_str(" (");
        output.push_str(&views.to_string());
        output.push_str(" views)</li>");
    }
    output.push_str("</ul>");
    output
}

fn title_case_header(value: &str) -> String {
    value
        .split('-')
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join("-")
}

/// Decode ASGI request headers (lowercase byte-pairs per the ASGI spec) into
/// the Title-Case string pairs `hayavega.asgi`'s request-building path uses,
/// matching `raw_name.decode("latin-1").title()` exactly for already-lowercase
/// ASGI header names.
pub fn decode_request_headers(headers: &[(Vec<u8>, Vec<u8>)]) -> Vec<(String, String)> {
    headers.iter().map(|(name, value)| (title_case_header(&String::from_utf8_lossy(name)), String::from_utf8_lossy(value).into_owned())).collect()
}

/// Encode (name, value) response headers into the lowercase byte-pairs ASGI requires.
pub fn encode_response_headers(headers: &[(String, String)]) -> Vec<(Vec<u8>, Vec<u8>)> {
    headers.iter().map(|(name, value)| (name.to_lowercase().into_bytes(), value.clone().into_bytes())).collect()
}

// ---------------------------------------------------------------------------
// Template rendering: a general interpreter for the non-inheriting subset of
// hayavega.templates's node language -- variable interpolation, if/elif/else,
// and for loops -- mirroring templates.py's `_evaluate`/`_render_nodes`
// semantics exactly. `{% extends %}`/`{% block %}` are deliberately not
// represented here: the Python side refuses to produce wire nodes for a
// template that uses them, and callers fall back to the pure-Python
// renderer in that case. See benchmarks/native_template_engine.py, which is
// the only caller of this today -- it is not yet wired into
// `hayavega.templates.render_template`.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum TemplateExpr {
    Literal(JsonValue),
    Path(String),
    Not(Box<TemplateExpr>),
    Compare(Box<TemplateExpr>, String, Box<TemplateExpr>),
    BoolOp(String, Vec<TemplateExpr>),
}

#[derive(Clone, Debug)]
pub enum TemplateNode {
    Text(String),
    Variable(TemplateExpr),
    If(Vec<(Option<TemplateExpr>, Vec<TemplateNode>)>),
    For { var_name: String, iterable: TemplateExpr, body: Vec<TemplateNode> },
}

fn json_get<'a>(object: &'a JsonValue, key: &str) -> Option<&'a JsonValue> {
    match object {
        JsonValue::Object(entries) => entries.iter().find(|(name, _)| name == key).map(|(_, value)| value),
        _ => None,
    }
}

fn resolve_path<'a>(context: &'a JsonValue, dotted: &str) -> Option<&'a JsonValue> {
    let mut parts = dotted.split('.');
    let mut current = json_get(context, parts.next()?)?;
    for part in parts {
        current = json_get(current, part)?;
    }
    Some(current)
}

/// Python truthiness for a resolved value; `None` (an undefined path) is falsy,
/// matching templates.py's `_truthy`.
fn truthy(value: Option<&JsonValue>) -> bool {
    match value {
        None => false,
        Some(JsonValue::Null) => false,
        Some(JsonValue::Bool(flag)) => *flag,
        Some(JsonValue::Number(text)) => text.parse::<f64>().map(|number| number != 0.0).unwrap_or(true),
        Some(JsonValue::String(text)) => !text.is_empty(),
        Some(JsonValue::Array(items)) => !items.is_empty(),
        Some(JsonValue::Object(entries)) => !entries.is_empty(),
    }
}

/// An undefined path compares like `None`, matching templates.py's `_as_comparable`.
fn as_comparable(value: Option<JsonValue>) -> JsonValue {
    value.unwrap_or(JsonValue::Null)
}

fn contains(container: &JsonValue, needle: &JsonValue) -> bool {
    match container {
        JsonValue::Array(items) => items.contains(needle),
        JsonValue::Object(entries) => matches!(needle, JsonValue::String(key) if entries.iter().any(|(name, _)| name == key)),
        JsonValue::String(haystack) => matches!(needle, JsonValue::String(text) if haystack.contains(text.as_str())),
        _ => false,
    }
}

/// `str(value)` for the value types templates.py's real templates actually
/// interpolate (scalars). Whole dicts/lists have no faithful Python `str()`
/// equivalent here and are rendered as JSON instead -- a disclosed gap, not
/// expected to matter since interpolating a whole mapping is not a pattern
/// this benchmark or its source template use.
fn display_value(value: &JsonValue) -> String {
    match value {
        JsonValue::Null => "None".to_owned(),
        JsonValue::Bool(true) => "True".to_owned(),
        JsonValue::Bool(false) => "False".to_owned(),
        JsonValue::Number(text) => text.clone(),
        JsonValue::String(text) => text.clone(),
        JsonValue::Array(_) | JsonValue::Object(_) => value.encode(),
    }
}

fn evaluate(expr: &TemplateExpr, context: &JsonValue) -> Option<JsonValue> {
    match expr {
        TemplateExpr::Literal(value) => Some(value.clone()),
        TemplateExpr::Path(dotted) => resolve_path(context, dotted).cloned(),
        TemplateExpr::Not(operand) => Some(JsonValue::Bool(!truthy(evaluate(operand, context).as_ref()))),
        TemplateExpr::Compare(left, op, right) => {
            let left_value = as_comparable(evaluate(left, context));
            let right_value = as_comparable(evaluate(right, context));
            let result = match op.as_str() {
                "==" => left_value == right_value,
                "!=" => left_value != right_value,
                "in" => contains(&right_value, &left_value),
                _ => false,
            };
            Some(JsonValue::Bool(result))
        }
        TemplateExpr::BoolOp(kind, operands) => {
            let mut truths = operands.iter().map(|operand| truthy(evaluate(operand, context).as_ref()));
            let result = if kind == "and" { truths.all(|value| value) } else { truths.any(|value| value) };
            Some(JsonValue::Bool(result))
        }
    }
}

fn object_set(entries: &mut Vec<(String, JsonValue)>, key: &str, value: JsonValue) {
    match entries.iter_mut().find(|(name, _)| name == key) {
        Some(existing) => existing.1 = value,
        None => entries.push((key.to_owned(), value)),
    }
}

fn render_nodes_into(nodes: &[TemplateNode], context: &JsonValue, output: &mut String) {
    for node in nodes {
        match node {
            TemplateNode::Text(text) => output.push_str(text),
            TemplateNode::Variable(expr) => {
                if let Some(value) = evaluate(expr, context) {
                    output.push_str(&escape_html(&display_value(&value)));
                }
                // An undefined path renders as nothing, matching templates.py.
            }
            TemplateNode::If(branches) => {
                for (condition, body) in branches {
                    let matched = match condition {
                        None => true,
                        Some(expr) => truthy(evaluate(expr, context).as_ref()),
                    };
                    if matched {
                        render_nodes_into(body, context, output);
                        break;
                    }
                }
            }
            TemplateNode::For { var_name, iterable, body } => {
                let items: Vec<JsonValue> = match evaluate(iterable, context) {
                    None | Some(JsonValue::Null) => Vec::new(),
                    Some(JsonValue::String(text)) if text.is_empty() => Vec::new(),
                    Some(JsonValue::String(text)) => text.chars().map(|character| JsonValue::String(character.to_string())).collect(),
                    Some(JsonValue::Array(items)) => items,
                    Some(JsonValue::Object(entries)) => entries.into_iter().map(|(key, _)| JsonValue::String(key)).collect(),
                    Some(_) => Vec::new(),
                };
                let count = items.len();
                for (index, item) in items.into_iter().enumerate() {
                    let mut entries = match context {
                        JsonValue::Object(existing) => existing.clone(),
                        _ => Vec::new(),
                    };
                    object_set(&mut entries, var_name, item);
                    let loop_meta = JsonValue::Object(vec![
                        ("index".to_owned(), JsonValue::Number((index + 1).to_string())),
                        ("index0".to_owned(), JsonValue::Number(index.to_string())),
                        ("first".to_owned(), JsonValue::Bool(index == 0)),
                        ("last".to_owned(), JsonValue::Bool(index + 1 == count)),
                    ]);
                    object_set(&mut entries, "loop", loop_meta);
                    render_nodes_into(body, &JsonValue::Object(entries), output);
                }
            }
        }
    }
}

/// Render a compiled node tree against a context, matching
/// templates.py's `_render_nodes` for the variable/if/for subset of nodes.
pub fn render_template_nodes(nodes: &[TemplateNode], context: &JsonValue) -> String {
    let mut output = String::new();
    render_nodes_into(nodes, context, &mut output);
    output
}

/// A compact, Rust-owned growable sequence used by the Python binding.
#[derive(Clone, Debug, Default)]
pub struct MangoList<T> {
    items: Vec<T>,
}

fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    match chars.next() {
        Some(first) if first.is_ascii_alphabetic() || first == '_' => chars.all(|character| character.is_ascii_alphanumeric() || character == '_'),
        _ => false,
    }
}

/// A validated SQL selection plan. Values stay parameterized in the host layer.
#[derive(Clone, Debug)]
pub struct QueryPlan {
    table: String,
    fields: Vec<String>,
    conditions: Vec<(String, String)>,
    ordering: Vec<(String, String)>,
    limit: Option<usize>,
    _offset: usize,
}

/// JSON value representation used by the native presentation layer.
#[derive(Clone, Debug, PartialEq)]
pub enum JsonValue {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<JsonValue>),
    Object(Vec<(String, JsonValue)>),
}

fn quote_json(value: &str) -> String {
    let mut output = String::from("\"");
    for character in value.chars() {
        match character {
            '\"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => output.push_str(&format!("\\u{:04x}", character as u32)),
            character => output.push(character),
        }
    }
    output.push('\"');
    output
}

impl JsonValue {
    pub fn encode(&self) -> String {
        match self {
            Self::Null => "null".to_owned(),
            Self::Bool(value) => value.to_string(),
            Self::Number(value) => value.clone(),
            Self::String(value) => quote_json(value),
            Self::Array(values) => format!("[{}]", values.iter().map(Self::encode).collect::<Vec<_>>().join(",")),
            Self::Object(values) => format!("{{{}}}", values.iter().map(|(key, value)| format!("{}:{}", quote_json(key), value.encode())).collect::<Vec<_>>().join(",")),
        }
    }
}

impl QueryPlan {
    pub fn new(
        table: String,
        fields: Vec<String>,
        conditions: Vec<(String, String)>,
        ordering: Vec<(String, String)>,
        limit: Option<usize>,
        offset: usize,
    ) -> Result<Self, String> {
        if !valid_identifier(&table) || fields.iter().any(|field| !valid_identifier(field)) {
            return Err("unsafe SQL identifier".to_owned());
        }
        if conditions.iter().any(|(field, operator)| !valid_identifier(field) || !matches!(operator.as_str(), "=" | "!=" | ">" | ">=" | "<" | "<=" | "LIKE")) {
            return Err("unsafe selection condition".to_owned());
        }
        if ordering.iter().any(|(field, direction)| !valid_identifier(field) || !matches!(direction.as_str(), "ASC" | "DESC")) {
            return Err("unsafe ordering".to_owned());
        }
        Ok(Self { table, fields, conditions, ordering, limit, _offset: offset })
    }

    pub fn compile_select(&self) -> String {
        let fields = if self.fields.is_empty() { "*".to_owned() } else { self.fields.join(", ") };
        let mut sql = format!("SELECT {fields} FROM {}", self.table);
        if !self.conditions.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&self.conditions.iter().map(|(field, operator)| format!("{field} {operator} ?")).collect::<Vec<_>>().join(" AND "));
        }
        if !self.ordering.is_empty() {
            sql.push_str(" ORDER BY ");
            sql.push_str(&self.ordering.iter().map(|(field, direction)| format!("{field} {direction}")).collect::<Vec<_>>().join(", "));
        }
        if self.limit.is_some() {
            sql.push_str(" LIMIT ? OFFSET ?");
        }
        sql
    }
}

impl<T> MangoList<T> {
    pub fn new(items: Vec<T>) -> Self { Self { items } }
    pub fn len(&self) -> usize { self.items.len() }
    pub fn is_empty(&self) -> bool { self.items.is_empty() }
    pub fn append(&mut self, item: T) { self.items.push(item); }
    pub fn insert(&mut self, index: usize, item: T) { self.items.insert(index, item); }
    pub fn remove(&mut self, index: usize) -> T { self.items.remove(index) }
    pub fn clear(&mut self) { self.items.clear(); }
    pub fn as_slice(&self) -> &[T] { &self.items }
    pub fn as_mut_slice(&mut self) -> &mut [T] { &mut self.items }
    pub fn into_vec(self) -> Vec<T> { self.items }
}

// ---------------------------------------------------------------------------
// GraphQL: lexing + parsing only. Field resolution calls arbitrary Python
// callables and can never move here -- see RUST_CORE_REWRITE_ROADMAP.md.
// Mirrors src/hayavega/graphql.py's `_tokenize`/`_Parser` grammar exactly
// (same punctuation set, same literal lexing, same rejected constructs).
// ---------------------------------------------------------------------------

const GRAPHQL_MAX_STRUCTURAL_DEPTH: u32 = 64;

#[derive(Clone, Debug, PartialEq)]
pub enum GraphQLValue {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
    Variable(String),
    List(Vec<GraphQLValue>),
    Object(Vec<(String, GraphQLValue)>),
}

#[derive(Clone, Debug, PartialEq)]
pub struct GraphQLVariableDefinition {
    pub name: String,
    pub required: bool,
    pub default: Option<GraphQLValue>,
    pub has_default: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct GraphQLField {
    pub name: String,
    pub arguments: Vec<(String, GraphQLValue)>,
    pub selection_set: Option<Vec<GraphQLField>>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct GraphQLDocument {
    pub operation_type: String,
    pub operation_name: Option<String>,
    pub variable_definitions: Vec<GraphQLVariableDefinition>,
    pub selection_set: Vec<GraphQLField>,
}

#[derive(Clone, Debug, PartialEq)]
enum GqlToken {
    Punct(char),
    Name(String),
    Int(i64),
    Float(f64),
    Str(String),
    Eof,
}

fn decode_graphql_string(raw: &str) -> Result<String, String> {
    // `raw` includes the surrounding quotes.
    let inner = &raw[1..raw.len() - 1];
    let mut result = String::new();
    let mut chars = inner.chars();
    while let Some(character) = chars.next() {
        if character != '\\' {
            result.push(character);
            continue;
        }
        match chars.next() {
            Some('"') => result.push('"'),
            Some('\\') => result.push('\\'),
            Some('/') => result.push('/'),
            Some('b') => result.push('\u{0008}'),
            Some('f') => result.push('\u{000C}'),
            Some('n') => result.push('\n'),
            Some('r') => result.push('\r'),
            Some('t') => result.push('\t'),
            Some('u') => {
                let hex: String = chars.by_ref().take(4).collect();
                if hex.len() != 4 {
                    return Err("Invalid \\u escape in string literal.".to_owned());
                }
                let code = u32::from_str_radix(&hex, 16).map_err(|_| "Invalid \\u escape in string literal.".to_owned())?;
                result.push(char::from_u32(code).ok_or_else(|| "Invalid \\u escape in string literal.".to_owned())?);
            }
            _ => return Err("Invalid escape sequence in string literal.".to_owned()),
        }
    }
    Ok(result)
}

fn tokenize_graphql(source: &str) -> Result<Vec<GqlToken>, String> {
    let chars: Vec<char> = source.chars().collect();
    let length = chars.len();
    let mut index = 0;
    let mut tokens = Vec::new();
    while index < length {
        let character = chars[index];
        if character.is_whitespace() || character == ',' || character == '\u{FEFF}' {
            index += 1;
            continue;
        }
        if character == '#' {
            while index < length && chars[index] != '\n' && chars[index] != '\r' {
                index += 1;
            }
            continue;
        }
        if character == '"' {
            let start = index;
            let mut end = index + 1;
            while end < length && chars[end] != '"' {
                end += if chars[end] == '\\' { 2 } else { 1 };
            }
            if end >= length {
                return Err("Unterminated string literal.".to_owned());
            }
            let raw: String = chars[start..=end].iter().collect();
            tokens.push(GqlToken::Str(decode_graphql_string(&raw)?));
            index = end + 1;
            continue;
        }
        if "{}():$[]!=".contains(character) {
            tokens.push(GqlToken::Punct(character));
            index += 1;
            continue;
        }
        if character.is_ascii_alphabetic() || character == '_' {
            let start = index;
            while index < length && (chars[index].is_ascii_alphanumeric() || chars[index] == '_') {
                index += 1;
            }
            tokens.push(GqlToken::Name(chars[start..index].iter().collect()));
            continue;
        }
        if character.is_ascii_digit() || (character == '-' && index + 1 < length && chars[index + 1].is_ascii_digit()) {
            let start = index;
            index += 1;
            let mut is_float = false;
            while index < length && chars[index].is_ascii_digit() {
                index += 1;
            }
            if index < length && chars[index] == '.' {
                is_float = true;
                index += 1;
                while index < length && chars[index].is_ascii_digit() {
                    index += 1;
                }
            }
            if index < length && (chars[index] == 'e' || chars[index] == 'E') {
                is_float = true;
                index += 1;
                if index < length && (chars[index] == '+' || chars[index] == '-') {
                    index += 1;
                }
                while index < length && chars[index].is_ascii_digit() {
                    index += 1;
                }
            }
            let raw: String = chars[start..index].iter().collect();
            if is_float {
                tokens.push(GqlToken::Float(raw.parse().map_err(|_| format!("Invalid number literal: {raw}"))?));
            } else {
                tokens.push(GqlToken::Int(raw.parse().map_err(|_| format!("Invalid number literal: {raw}"))?));
            }
            continue;
        }
        if character == '@' {
            return Err("Directives are not supported.".to_owned());
        }
        if character == '.' {
            return Err("Fragments are not supported.".to_owned());
        }
        return Err(format!("Unexpected character {character:?} in query."));
    }
    tokens.push(GqlToken::Eof);
    Ok(tokens)
}

struct GraphQLParser {
    tokens: Vec<GqlToken>,
    pos: usize,
    depth: u32,
}

impl GraphQLParser {
    fn new(tokens: Vec<GqlToken>) -> Self {
        Self { tokens, pos: 0, depth: 0 }
    }

    fn peek(&self) -> &GqlToken {
        &self.tokens[self.pos]
    }

    fn advance(&mut self) -> GqlToken {
        let token = self.tokens[self.pos].clone();
        if self.pos + 1 < self.tokens.len() {
            self.pos += 1;
        }
        token
    }

    fn peek_is_punct(&self, value: char) -> bool {
        matches!(self.peek(), GqlToken::Punct(character) if *character == value)
    }

    fn expect_punct(&mut self, value: char) -> Result<(), String> {
        match self.advance() {
            GqlToken::Punct(character) if character == value => Ok(()),
            other => Err(format!("Expected '{value}', found {other:?}.")),
        }
    }

    fn expect_name(&mut self) -> Result<String, String> {
        match self.advance() {
            GqlToken::Name(name) => Ok(name),
            other => Err(format!("Expected a name, found {other:?}.")),
        }
    }

    fn parse_document(&mut self) -> Result<GraphQLDocument, String> {
        let mut operation_type = "query".to_owned();
        let mut operation_name = None;
        if let GqlToken::Name(name) = self.peek() {
            if name == "query" || name == "mutation" {
                operation_type = self.expect_name()?;
                if matches!(self.peek(), GqlToken::Name(_)) {
                    operation_name = Some(self.expect_name()?);
                }
            }
        }
        let variable_definitions = self.parse_variable_definitions()?;
        let selection_set = self.parse_selection_set()?;
        if !matches!(self.peek(), GqlToken::Eof) {
            return Err("Unexpected content after the operation.".to_owned());
        }
        Ok(GraphQLDocument { operation_type, operation_name, variable_definitions, selection_set })
    }

    fn parse_variable_definitions(&mut self) -> Result<Vec<GraphQLVariableDefinition>, String> {
        let mut definitions = Vec::new();
        if !self.peek_is_punct('(') {
            return Ok(definitions);
        }
        self.advance();
        while !self.peek_is_punct(')') {
            self.expect_punct('$')?;
            let name = self.expect_name()?;
            self.expect_punct(':')?;
            let non_null = self.parse_type()?;
            let mut has_default = false;
            let mut default = None;
            if self.peek_is_punct('=') {
                self.advance();
                default = Some(self.parse_value()?);
                has_default = true;
            }
            definitions.push(GraphQLVariableDefinition { name, required: non_null && !has_default, default, has_default });
        }
        self.advance();
        Ok(definitions)
    }

    fn parse_type(&mut self) -> Result<bool, String> {
        if self.peek_is_punct('[') {
            self.advance();
            self.parse_type()?;
            self.expect_punct(']')?;
        } else {
            self.expect_name()?;
        }
        if self.peek_is_punct('!') {
            self.advance();
            return Ok(true);
        }
        Ok(false)
    }

    fn parse_selection_set(&mut self) -> Result<Vec<GraphQLField>, String> {
        self.depth += 1;
        if self.depth > GRAPHQL_MAX_STRUCTURAL_DEPTH {
            return Err("Query is nested too deeply to parse.".to_owned());
        }
        self.expect_punct('{')?;
        let mut fields = Vec::new();
        while !self.peek_is_punct('}') {
            fields.push(self.parse_field()?);
        }
        self.advance();
        if fields.is_empty() {
            return Err("A selection set must select at least one field.".to_owned());
        }
        self.depth -= 1;
        Ok(fields)
    }

    fn parse_field(&mut self) -> Result<GraphQLField, String> {
        let name = self.expect_name()?;
        let arguments = self.parse_arguments()?;
        let selection_set = if self.peek_is_punct('{') { Some(self.parse_selection_set()?) } else { None };
        Ok(GraphQLField { name, arguments, selection_set })
    }

    fn parse_arguments(&mut self) -> Result<Vec<(String, GraphQLValue)>, String> {
        let mut arguments = Vec::new();
        if !self.peek_is_punct('(') {
            return Ok(arguments);
        }
        self.advance();
        while !self.peek_is_punct(')') {
            let name = self.expect_name()?;
            self.expect_punct(':')?;
            arguments.push((name, self.parse_value()?));
        }
        self.advance();
        Ok(arguments)
    }

    fn parse_value(&mut self) -> Result<GraphQLValue, String> {
        match self.peek().clone() {
            GqlToken::Punct('$') => {
                self.advance();
                Ok(GraphQLValue::Variable(self.expect_name()?))
            }
            GqlToken::Int(value) => {
                self.advance();
                Ok(GraphQLValue::Int(value))
            }
            GqlToken::Float(value) => {
                self.advance();
                Ok(GraphQLValue::Float(value))
            }
            GqlToken::Str(value) => {
                self.advance();
                Ok(GraphQLValue::String(value))
            }
            GqlToken::Name(name) => {
                self.advance();
                match name.as_str() {
                    "true" => Ok(GraphQLValue::Bool(true)),
                    "false" => Ok(GraphQLValue::Bool(false)),
                    "null" => Ok(GraphQLValue::Null),
                    _ => Ok(GraphQLValue::String(name)),
                }
            }
            GqlToken::Punct('[') => {
                self.advance();
                let mut items = Vec::new();
                while !self.peek_is_punct(']') {
                    items.push(self.parse_value()?);
                }
                self.advance();
                Ok(GraphQLValue::List(items))
            }
            GqlToken::Punct('{') => {
                self.advance();
                let mut fields = Vec::new();
                while !self.peek_is_punct('}') {
                    let key = self.expect_name()?;
                    self.expect_punct(':')?;
                    fields.push((key, self.parse_value()?));
                }
                self.advance();
                Ok(GraphQLValue::Object(fields))
            }
            other => Err(format!("Expected a value, found {other:?}.")),
        }
    }
}

pub fn parse_graphql_document(source: &str) -> Result<GraphQLDocument, String> {
    let tokens = tokenize_graphql(source)?;
    GraphQLParser::new(tokens).parse_document()
}

#[cfg(test)]
mod tests {
    use super::{decode_request_headers, encode_response_headers, normalize_path, parse_graphql_document, render_fragment_items, render_post_list_page, render_template_nodes, GraphQLValue, JsonValue, MangoList, QueryPlan, TemplateExpr, TemplateNode};

    #[test]
    fn normalizes_paths() {
        assert_eq!(normalize_path("posts/"), "/posts");
    }

    #[test]
    fn mango_list_preserves_order() {
        let mut values = MangoList::new(vec![1, 2]);
        values.append(3);
        values.insert(1, 9);
        assert_eq!(values.as_slice(), &[1, 9, 2, 3]);
        assert_eq!(values.remove(2), 2);
    }

    #[test]
    fn query_plan_compiles_parameterized_selection() {
        let plan = QueryPlan::new(
            "product_entries".to_owned(),
            vec!["id".to_owned(), "name".to_owned()],
            vec![("published".to_owned(), "=".to_owned())],
            vec![("name".to_owned(), "ASC".to_owned())],
            Some(10),
            0,
        ).unwrap();
        assert_eq!(plan.compile_select(), "SELECT id, name FROM product_entries WHERE published = ? ORDER BY name ASC LIMIT ? OFFSET ?");
    }

    #[test]
    fn json_encoder_escapes_and_nests_values() {
        let value = JsonValue::Object(vec![
            ("name".to_owned(), JsonValue::String("Notebook\nA5".to_owned())),
            ("tags".to_owned(), JsonValue::Array(vec![JsonValue::String("paper".to_owned()), JsonValue::Bool(true)])),
        ]);
        assert_eq!(value.encode(), "{\"name\":\"Notebook\\nA5\",\"tags\":[\"paper\",true]}");
    }

    #[test]
    fn renders_post_list_page_matching_the_python_template() {
        let posts = vec![("Post 0".to_owned(), 0), ("Post 1".to_owned(), 3)];
        let rendered = render_post_list_page(&posts);
        assert_eq!(rendered, "<!doctype html>\n<html>\n<head><title>Posts</title></head>\n<body>\n<h1>Posts</h1>\n<ul>\n\n<li>Post 0 (0 views)</li>\n\n<li>Post 1 (3 views)</li>\n\n</ul>\n</body>\n</html>\n");
    }

    #[test]
    fn renders_fragment_items_matching_hayavega_ui_output() {
        let items = vec![("Post 0".to_owned(), 0), ("Post 1".to_owned(), 3)];
        let rendered = render_fragment_items(&items);
        assert_eq!(rendered, "<ul><li>Post 0 (0 views)</li><li>Post 1 (3 views)</li></ul>");
    }

    #[test]
    fn decode_request_headers_title_cases_names() {
        let headers = vec![(b"x-request-id".to_vec(), b"abc".to_vec()), (b"host".to_vec(), b"example.com".to_vec())];
        assert_eq!(decode_request_headers(&headers), vec![("X-Request-Id".to_owned(), "abc".to_owned()), ("Host".to_owned(), "example.com".to_owned())]);
    }

    #[test]
    fn encode_response_headers_lowercases_names() {
        let headers = vec![("X-Request-ID".to_owned(), "abc".to_owned())];
        assert_eq!(encode_response_headers(&headers), vec![(b"x-request-id".to_vec(), b"abc".to_vec())]);
    }

    fn object(entries: Vec<(&str, JsonValue)>) -> JsonValue {
        JsonValue::Object(entries.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
    }

    #[test]
    fn template_variable_escapes_and_renders_undefined_as_blank() {
        let nodes = vec![TemplateNode::Variable(TemplateExpr::Path("name".to_owned())), TemplateNode::Variable(TemplateExpr::Path("missing".to_owned()))];
        let context = object(vec![("name", JsonValue::String("<b>Ann</b>".to_owned()))]);
        assert_eq!(render_template_nodes(&nodes, &context), "&lt;b&gt;Ann&lt;/b&gt;");
    }

    #[test]
    fn template_variable_distinguishes_null_from_undefined() {
        let context = object(vec![("value", JsonValue::Null)]);
        assert_eq!(render_template_nodes(&[TemplateNode::Variable(TemplateExpr::Path("value".to_owned()))], &context), "None");
        assert_eq!(render_template_nodes(&[TemplateNode::Variable(TemplateExpr::Path("missing".to_owned()))], &context), "");
    }

    #[test]
    fn template_if_elif_else_picks_first_true_branch() {
        let nodes = vec![TemplateNode::If(vec![
            (Some(TemplateExpr::Compare(Box::new(TemplateExpr::Path("views".to_owned())), "==".to_owned(), Box::new(TemplateExpr::Literal(JsonValue::Number("0".to_owned()))))), vec![TemplateNode::Text("zero".to_owned())]),
            (Some(TemplateExpr::Path("views".to_owned())), vec![TemplateNode::Text("nonzero".to_owned())]),
            (None, vec![TemplateNode::Text("else".to_owned())]),
        ])];
        assert_eq!(render_template_nodes(&nodes, &object(vec![("views", JsonValue::Number("0".to_owned()))])), "zero");
        assert_eq!(render_template_nodes(&nodes, &object(vec![("views", JsonValue::Number("5".to_owned()))])), "nonzero");
        assert_eq!(render_template_nodes(&nodes, &object(vec![])), "else");
    }

    #[test]
    fn template_for_loop_injects_item_and_loop_metadata() {
        let nodes = vec![TemplateNode::For {
            var_name: "item".to_owned(),
            iterable: TemplateExpr::Path("items".to_owned()),
            body: vec![
                TemplateNode::Variable(TemplateExpr::Path("item".to_owned())),
                TemplateNode::If(vec![(Some(TemplateExpr::Path("loop.first".to_owned())), vec![TemplateNode::Text("(first)".to_owned())]), (None, vec![])]),
                TemplateNode::If(vec![(Some(TemplateExpr::Path("loop.last".to_owned())), vec![TemplateNode::Text("(last)".to_owned())]), (None, vec![])]),
            ],
        }];
        let context = object(vec![("items", JsonValue::Array(vec![JsonValue::String("a".to_owned()), JsonValue::String("b".to_owned())]))]);
        assert_eq!(render_template_nodes(&nodes, &context), "a(first)b(last)");
    }

    #[test]
    fn template_for_loop_over_undefined_iterable_renders_nothing() {
        let nodes = vec![TemplateNode::For { var_name: "item".to_owned(), iterable: TemplateExpr::Path("missing".to_owned()), body: vec![TemplateNode::Text("x".to_owned())] }];
        assert_eq!(render_template_nodes(&nodes, &object(vec![])), "");
    }

    #[test]
    fn template_matches_the_real_page_html_output() {
        // Mirrors what benchmarks/hayavega_app/templates/page.html compiles to
        // for the variable/for subset (no extends/block in that template).
        let nodes = vec![
            TemplateNode::Text("<ul>\n".to_owned()),
            TemplateNode::For {
                var_name: "post".to_owned(),
                iterable: TemplateExpr::Path("posts".to_owned()),
                body: vec![
                    TemplateNode::Text("\n<li>".to_owned()),
                    TemplateNode::Variable(TemplateExpr::Path("post.title".to_owned())),
                    TemplateNode::Text(" (".to_owned()),
                    TemplateNode::Variable(TemplateExpr::Path("post.views".to_owned())),
                    TemplateNode::Text(" views)</li>\n".to_owned()),
                ],
            },
            TemplateNode::Text("\n</ul>\n".to_owned()),
        ];
        let posts = JsonValue::Array(vec![object(vec![("title", JsonValue::String("Post 0".to_owned())), ("views", JsonValue::Number("0".to_owned()))]), object(vec![("title", JsonValue::String("Post 1".to_owned())), ("views", JsonValue::Number("3".to_owned()))])]);
        let context = object(vec![("posts", posts)]);
        assert_eq!(render_template_nodes(&nodes, &context), "<ul>\n\n<li>Post 0 (0 views)</li>\n\n<li>Post 1 (3 views)</li>\n\n</ul>\n");
    }

    #[test]
    fn graphql_parses_a_minimal_query() {
        let document = parse_graphql_document("{ greeting }").unwrap();
        assert_eq!(document.operation_type, "query");
        assert_eq!(document.operation_name, None);
        assert!(document.variable_definitions.is_empty());
        assert_eq!(document.selection_set.len(), 1);
        assert_eq!(document.selection_set[0].name, "greeting");
        assert!(document.selection_set[0].arguments.is_empty());
        assert_eq!(document.selection_set[0].selection_set, None);
    }

    #[test]
    fn graphql_parses_a_complex_query_with_variables_and_nesting() {
        let source = r#"
            query ListPosts($limit: Int! = 10, $search: String) {
                posts(limit: $limit, search: $search, tags: ["rust", "web"]) {
                    id
                    title
                    author {
                        name
                    }
                }
                viewer {
                    id
                }
            }
        "#;
        let document = parse_graphql_document(source).unwrap();
        assert_eq!(document.operation_type, "query");
        assert_eq!(document.operation_name, Some("ListPosts".to_owned()));
        assert_eq!(document.variable_definitions.len(), 2);
        assert_eq!(document.variable_definitions[0].name, "limit");
        assert!(!document.variable_definitions[0].required);
        assert_eq!(document.variable_definitions[0].default, Some(GraphQLValue::Int(10)));
        assert_eq!(document.variable_definitions[1].name, "search");
        assert!(!document.variable_definitions[1].required);
        assert!(!document.variable_definitions[1].has_default);

        assert_eq!(document.selection_set.len(), 2);
        let posts = &document.selection_set[0];
        assert_eq!(posts.name, "posts");
        assert_eq!(posts.arguments.len(), 3);
        assert_eq!(posts.arguments[0], ("limit".to_owned(), GraphQLValue::Variable("limit".to_owned())));
        assert_eq!(posts.arguments[2], ("tags".to_owned(), GraphQLValue::List(vec![GraphQLValue::String("rust".to_owned()), GraphQLValue::String("web".to_owned())])));
        let posts_selection = posts.selection_set.as_ref().unwrap();
        assert_eq!(posts_selection.len(), 3);
        assert_eq!(posts_selection[2].name, "author");
        assert_eq!(posts_selection[2].selection_set.as_ref().unwrap()[0].name, "name");

        assert_eq!(document.selection_set[1].name, "viewer");
    }

    #[test]
    fn graphql_rejects_directives() {
        assert!(parse_graphql_document("{ greeting @skip }").is_err());
    }

    #[test]
    fn graphql_rejects_fragments() {
        assert!(parse_graphql_document("{ ...fragment }").is_err());
    }

    #[test]
    fn graphql_rejects_empty_selection_sets() {
        assert!(parse_graphql_document("{ posts { } }").is_err());
    }

    #[test]
    fn graphql_rejects_overly_deep_nesting() {
        let mut source = String::new();
        for _ in 0..70 {
            source.push_str("{ a");
        }
        source.push_str(" x ");
        for _ in 0..70 {
            source.push('}');
        }
        assert!(parse_graphql_document(&source).is_err());
    }
}
