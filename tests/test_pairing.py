import time

from claude_option.telegram.pairing import attempt_pairing, create_pairing_code, get_owner, revoke_owner


def test_pairing_happy_path_and_persistence(db, tmp_path):
    code, _ = create_pairing_code(db)
    with db.connect() as conn:
        stored = conn.execute("SELECT code_hash FROM pairing_codes").fetchone()["code_hash"]
    assert code.replace("-", "") not in stored            # only the hash is stored
    ok, _ = attempt_pairing(db, code.lower(), user_id=11, chat_id=22, chat_type="private", username="me")
    assert ok
    owner = get_owner(db)
    assert (owner.telegram_user_id, owner.telegram_chat_id) == (11, 22)
    # survives a "restart"
    from claude_option.broker.database import Database
    assert get_owner(Database(db.path)).telegram_user_id == 11


def test_code_single_use_and_no_owner_replacement(db):
    code, _ = create_pairing_code(db)
    assert attempt_pairing(db, code, user_id=1, chat_id=1, chat_type="private", username=None)[0]
    ok, msg = attempt_pairing(db, code, user_id=2, chat_id=2, chat_type="private", username=None)
    assert not ok and "already has an owner" in msg
    assert get_owner(db).telegram_user_id == 1


def test_group_chat_refused(db):
    code, _ = create_pairing_code(db)
    ok, _ = attempt_pairing(db, code, user_id=1, chat_id=-100, chat_type="group", username=None)
    assert not ok and get_owner(db) is None


def test_new_code_invalidates_previous(db):
    old, _ = create_pairing_code(db)
    create_pairing_code(db)
    assert not attempt_pairing(db, old, user_id=1, chat_id=1, chat_type="private", username=None)[0]


def test_expired_code_refused(db):
    code, _ = create_pairing_code(db, ttl_seconds=1)
    time.sleep(1.1)
    assert not attempt_pairing(db, code, user_id=1, chat_id=1, chat_type="private", username=None)[0]


def test_brute_force_revokes_code(db):
    code, _ = create_pairing_code(db)
    for _ in range(5):
        attempt_pairing(db, "WRONG-CODE", user_id=9, chat_id=9, chat_type="private", username=None)
    assert not attempt_pairing(db, code, user_id=1, chat_id=1, chat_type="private", username=None)[0]


def test_revoke_owner(db):
    code, _ = create_pairing_code(db)
    attempt_pairing(db, code, user_id=1, chat_id=1, chat_type="private", username=None)
    assert revoke_owner(db) and get_owner(db) is None
