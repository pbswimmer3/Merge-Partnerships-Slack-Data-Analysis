from src.notion_writer import build_row_properties, ensure_database, upsert_question


def _base_question(**overrides):
    question = {
        "ts": "1700000000.000100",
        "user": "U123",
        "text": "How do I set up the webhook?",
        "category": "api_technical",
        "is_question": True,
        "reply_count": 2,
        "first_reply_latency_sec": 90,
        "permalink": "https://slack.com/archives/C1/p1700000000000100",
    }
    question.update(overrides)
    return question


def test_build_row_properties_maps_core_fields():
    props = build_row_properties(_base_question())

    assert props["Question"]["title"][0]["text"]["content"] == "How do I set up the webhook?"
    assert props["Date"]["date"]["start"] == "2023-11-14"
    assert props["Category"]["select"]["name"] == "api_technical"
    assert props["Automatable"]["checkbox"] is False
    assert props["Reply Count"]["number"] == 2
    assert props["First Reply Latency (min)"]["number"] == 1.5
    assert props["Slack User"]["rich_text"][0]["text"]["content"] == "U123"
    assert props["Message TS"]["rich_text"][0]["text"]["content"] == "1700000000.000100"
    assert props["Permalink"]["url"] == "https://slack.com/archives/C1/p1700000000000100"


def test_build_row_properties_truncates_long_title():
    long_text = "x" * 500
    props = build_row_properties(_base_question(text=long_text))
    assert len(props["Question"]["title"][0]["text"]["content"]) == 200


def test_build_row_properties_omits_none_optional_fields():
    question = _base_question(permalink=None, first_reply_latency_sec=None)
    question.pop("llm_category", None)
    props = build_row_properties(question)

    assert "Permalink" not in props
    assert "First Reply Latency (min)" not in props
    assert "LLM Category" not in props
    assert "Subtopic" not in props
    assert "Difficulty" not in props


def test_build_row_properties_includes_llm_fields_when_present():
    question = _base_question(llm_category="integrations", subtopic="Webhook setup", difficulty=3, automatable=True)
    props = build_row_properties(question)

    assert props["LLM Category"]["select"]["name"] == "integrations"
    assert props["Subtopic"]["rich_text"][0]["text"]["content"] == "Webhook setup"
    assert props["Difficulty"]["number"] == 3
    assert props["Automatable"]["checkbox"] is True


class _FakePagesEndpoint:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, parent, properties):
        self.created.append({"parent": parent, "properties": properties})

    def update(self, page_id, properties):
        self.updated.append({"page_id": page_id, "properties": properties})


class _FakeDatabasesEndpoint:
    def __init__(self, existing_page_id=None):
        self.existing_page_id = existing_page_id
        self.create_calls = []
        self.query_calls = []

    def create(self, parent, title, properties):
        self.create_calls.append({"parent": parent, "title": title, "properties": properties})
        return {"id": "new-db-id"}

    def query(self, database_id, filter):
        self.query_calls.append({"database_id": database_id, "filter": filter})
        if self.existing_page_id:
            return {"results": [{"id": self.existing_page_id}]}
        return {"results": []}


class _FakeClient:
    def __init__(self, existing_page_id=None):
        self.databases = _FakeDatabasesEndpoint(existing_page_id=existing_page_id)
        self.pages = _FakePagesEndpoint()


def test_ensure_database_returns_existing_id_without_creating():
    client = _FakeClient()
    result = ensure_database(client, parent_page_id="parent-1", database_id="existing-db")
    assert result == "existing-db"
    assert client.databases.create_calls == []


def test_ensure_database_creates_when_missing():
    client = _FakeClient()
    result = ensure_database(client, parent_page_id="parent-1", database_id=None)
    assert result == "new-db-id"
    assert len(client.databases.create_calls) == 1


def test_upsert_question_creates_when_no_existing_page():
    client = _FakeClient()
    upsert_question(client, "db-1", _base_question())
    assert len(client.pages.created) == 1
    assert len(client.pages.updated) == 0


def test_upsert_question_updates_when_existing_page():
    client = _FakeClient(existing_page_id="page-1")
    upsert_question(client, "db-1", _base_question())
    assert len(client.pages.updated) == 1
    assert client.pages.updated[0]["page_id"] == "page-1"
    assert len(client.pages.created) == 0
