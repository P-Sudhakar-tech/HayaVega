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

/// Render the exact HTML `benchmarks/mangoo_app/templates/page.html` produces
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
/// `mangoo.ui`'s `ul(*(li(...) for ...))` component builders produce for a
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
/// the Title-Case string pairs `mangoo.asgi`'s request-building path uses,
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
// mangoo.templates's node language -- variable interpolation, if/elif/else,
// and for loops -- mirroring templates.py's `_evaluate`/`_render_nodes`
// semantics exactly. `{% extends %}`/`{% block %}` are deliberately not
// represented here: the Python side refuses to produce wire nodes for a
// template that uses them, and callers fall back to the pure-Python
// renderer in that case. See benchmarks/native_template_engine.py, which is
// the only caller of this today -- it is not yet wired into
// `mangoo.templates.render_template`.
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

#[cfg(test)]
mod tests {
    use super::{decode_request_headers, encode_response_headers, normalize_path, render_fragment_items, render_post_list_page, render_template_nodes, JsonValue, MangoList, QueryPlan, TemplateExpr, TemplateNode};

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
    fn renders_fragment_items_matching_mangoo_ui_output() {
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
        // Mirrors what benchmarks/mangoo_app/templates/page.html compiles to
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
}
