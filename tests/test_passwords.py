"""密码哈希：新格式可用、老格式还能登、脏数据不崩。"""
import hashlib

from utils import hash_password, needs_rehash, verify_password

LEGACY_SALT = "smart_campus_2024_salt"


def _legacy_hash(password):
    return hashlib.sha256((password + LEGACY_SALT).encode("utf-8")).hexdigest()


def test_new_hash_uses_pbkdf2_and_random_salt():
    first = hash_password("admin123")
    second = hash_password("admin123")
    assert first.startswith("pbkdf2_sha256$200000$")
    assert first != second, "同一个密码两次哈希结果相同，说明盐没有随机化"


def test_verify_roundtrip():
    stored = hash_password("admin123")
    assert verify_password("admin123", stored) is True
    assert verify_password("admin124", stored) is False


def test_legacy_hash_still_verifies():
    """存量用户不会因为换算法而登不进来。"""
    assert verify_password("123456", _legacy_hash("123456")) is True
    assert verify_password("wrong", _legacy_hash("123456")) is False


def test_needs_rehash_marks_legacy_only():
    assert needs_rehash(_legacy_hash("123456")) is True
    assert needs_rehash(hash_password("123456")) is False
    assert needs_rehash("") is True


def test_malformed_stored_value_is_rejected():
    assert verify_password("x", "") is False
    assert verify_password("x", "pbkdf2_sha256$broken") is False
    assert verify_password("", hash_password("x")) is False
