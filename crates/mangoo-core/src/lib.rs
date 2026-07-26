//! Framework primitives intentionally independent of Python bindings.

pub fn normalize_path(path: &str) -> String {
    let trimmed = path.trim();
    if trimmed.is_empty() || trimmed == "/" {
        return "/".to_owned();
    }
    format!("/{}", trimmed.trim_matches('/'))
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
    use super::{normalize_path, JsonValue, MangoList, QueryPlan};

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
}
