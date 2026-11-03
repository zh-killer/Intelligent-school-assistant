"""SQL 白名单与 SSRF 拦截的单测。

这两处是「模型输出直接进执行链路」的闸门，改一行就可能放开一个口子，
所以用测试把当前策略钉住：允许什么、拒绝什么，都写在断言里。
"""
from security import _is_safe_url, _validate_sql


def test_select_on_allowed_table_is_ok():
    ok, reason = _validate_sql("SELECT * FROM students WHERE major = '人工智能'")
    assert ok, reason


def test_join_between_allowed_tables_is_ok():
    sql = (
        "SELECT s.student_name, sc.score FROM students s "
        "JOIN scores sc ON s.student_id = sc.student_id"
    )
    ok, reason = _validate_sql(sql)
    assert ok, reason


def test_ddl_is_rejected():
    for sql in ("DROP TABLE students", "ALTER TABLE students ADD COLUMN x INT"):
        ok, reason = _validate_sql(sql)
        assert not ok
        assert "仅允许" in reason or "禁止" in reason


def test_other_tables_are_rejected():
    ok, reason = _validate_sql("SELECT * FROM users")
    assert not ok
    assert "只允许查询" in reason


def test_update_without_where_is_rejected():
    ok, reason = _validate_sql("UPDATE students SET score = 0")
    assert not ok
    assert "WHERE" in reason


def test_update_with_where_is_ok():
    ok, reason = _validate_sql("UPDATE scores SET score = 95 WHERE student_id = 1")
    assert ok, reason


def test_delete_without_where_is_rejected():
    assert _validate_sql("DELETE FROM students")[0] is False


def test_multiple_statements_are_rejected():
    ok, reason = _validate_sql("SELECT * FROM students; DROP TABLE scores")
    assert not ok
    assert "多条语句" in reason


def test_empty_sql_is_rejected():
    assert _validate_sql("   ")[0] is False


def test_ssrf_blocks_private_targets():
    for url in (
        "http://127.0.0.1/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ):
        assert _is_safe_url(url) is False, url


def test_ssrf_allows_public_http():
    assert _is_safe_url("https://example.com/article") is True
