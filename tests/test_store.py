from src import store


def test_daily_analysis_path_creates_dir_and_uses_date_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DAILY_ANALYSIS_DIR", tmp_path / "analysis_by_day")
    path = store.daily_analysis_path("2026-07-10")
    assert path == tmp_path / "analysis_by_day" / "2026-07-10.json"
    assert path.parent.is_dir()


def test_read_all_daily_analysis_empty_dir_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DAILY_ANALYSIS_DIR", tmp_path / "analysis_by_day")
    assert store.read_all_daily_analysis() == []


def test_read_all_daily_analysis_round_trips_sorted_by_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DAILY_ANALYSIS_DIR", tmp_path / "analysis_by_day")
    store.write_json({"day": "2026-07-11"}, store.daily_analysis_path("2026-07-11"))
    store.write_json({"day": "2026-07-09"}, store.daily_analysis_path("2026-07-09"))
    store.write_json({"day": "2026-07-10"}, store.daily_analysis_path("2026-07-10"))

    results = store.read_all_daily_analysis()
    assert [r["day"] for r in results] == ["2026-07-09", "2026-07-10", "2026-07-11"]
