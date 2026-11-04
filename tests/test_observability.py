"""Observability (observability.py): TraceStore round trip, OTLP shape."""

import json

from coworker.observability import SessionTracer, TraceStore, to_otlp_json


def test_span_roundtrip(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    sid = store.start_span(session_id="s1", trace_id="s1", kind="tool", name="write_file", meta={"path": "a.txt"})
    store.finish_span(sid, status="ok", meta={"duration_ms": 42})
    spans = store.session_spans("s1")
    assert len(spans) == 1
    assert spans[0]["name"] == "write_file"
    assert spans[0]["status"] == "ok"
    assert spans[0]["duration_ms"] >= 0
    assert json.loads(spans[0]["meta"])["path"] == "a.txt"


def test_session_isolation(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    a = store.start_span(session_id="a", trace_id="a", kind="turn", name="turn")
    store.start_span(session_id="b", trace_id="b", kind="turn", name="turn")
    assert len(store.session_spans("a")) == 1
    store.finish_span(a, status="ok")
    assert store.session_spans("a")[0]["status"] == "ok"
    assert store.session_spans("b")[0]["status"] is None


def test_finish_nonexistent_span_noop(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    store.finish_span(999, status="ok")  # must not raise


def test_session_tracer_swallows_errors(tmp_path):
    tracer = SessionTracer(TraceStore(tmp_path / "traces.db"), "s1")
    sid = tracer.start_span("tool", "x")
    assert sid is not None
    tracer.finish(sid, status="ok")
    tracer.finish(None)  # no-op


def test_otlp_shape(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    sid = store.start_span(session_id="s1", trace_id="s1", kind="tool", name="read_file")
    store.finish_span(sid, status="ok", meta={"duration_ms": 120})
    spans = store.session_spans("s1")
    otlp = to_otlp_json(spans)
    rs = otlp["resourceSpans"][0]
    assert rs["resource"]["attributes"][0]["key"] == "service.name"
    ss = rs["scopeSpans"][0]["spans"]
    assert len(ss) == 1
    assert ss[0]["name"] == "tool/read_file"
    assert "startTimeUnixNano" in ss[0] and "endTimeUnixNano" in ss[0]
    keys = [a["key"] for a in ss[0]["attributes"]]
    assert "openworker.status" in keys and "openworker.duration_ms" in keys


def test_empty_session_otlp(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    otlp = to_otlp_json(store.session_spans("empty"))
    assert otlp["resourceSpans"][0]["scopeSpans"] == []
