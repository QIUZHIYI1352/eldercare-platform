"""P0 数据库兼容层纯逻辑自检（无需连接 MySQL）"""
from backend.database import convert_placeholders, split_script


def test_convert_placeholders():
    sql = "SELECT * FROM users WHERE id = ? AND role = ?"
    assert convert_placeholders(sql) == \
        "SELECT * FROM users WHERE id = %s AND role = %s"


def test_split_script_ignores_empty():
    sql = "CREATE TABLE a (x TEXT);\n\n; CREATE TABLE b (y TEXT);"
    assert split_script(sql) == \
        ["CREATE TABLE a (x TEXT)", "CREATE TABLE b (y TEXT)"]


if __name__ == "__main__":
    test_convert_placeholders()
    test_split_script_ignores_empty()
    print("OK: database compat checks passed")
