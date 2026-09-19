from workout_relay.security import TokenVault, hash_password, verify_password


def test_password_hash_and_token_encryption(settings):
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert "correct horse" not in encoded
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("incorrect", encoded)

    vault = TokenVault(settings.master_encryption_key)
    encrypted = vault.encrypt('{"refresh_token":"high-value-secret"}')
    assert "high-value-secret" not in encrypted
    assert vault.decrypt(encrypted) == '{"refresh_token":"high-value-secret"}'
